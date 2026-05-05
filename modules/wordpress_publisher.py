"""
Publica imoveis no WordPress via REST API.
Substitui o MercadoiDriver (Playwright) na Fase 1 do plano v4.0.

Suporta dois modos de autenticação:
  - Application Passwords (recomendado): wp_user + wp_app_password → Basic Auth
  - API Key (plugin legado):             api_key                   → Bearer token
"""

import os
import base64
import asyncio
import httpx
from modules.logger import Logger
from modules.property_types import aplicar_tipos_imovel

logger = Logger("wordpress_publisher")

TIMEOUT = 60
UPLOAD_TIMEOUT = 120


class WordPressPublisher:
    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        wp_user: str = "",
        wp_app_password: str = "",
        execution_id: str = "",
    ):
        self.api_url      = api_url.rstrip("/")
        self.execution_id = execution_id
        self._headers     = self._build_auth_header(api_key, wp_user, wp_app_password)

    @staticmethod
    def _build_auth_header(api_key: str, wp_user: str, wp_app_password: str) -> dict:
        if wp_user and wp_app_password:
            # Application Passwords: Basic Auth com usuário WordPress
            token = base64.b64encode(f"{wp_user}:{wp_app_password}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if api_key:
            # Legado: plugin com Bearer token
            return {"Authorization": f"Bearer {api_key}"}
        return {}

    # ------------------------------------------------------------------
    # Interface pública — mesma assinatura que MercadoiDriver
    # ------------------------------------------------------------------

    async def preencher_e_salvar(self, dados: dict, tipo_midia: str, arquivo_midia: list) -> dict:
        dados = aplicar_tipos_imovel(dict(dados or {}))
        resultado = {
            "sucesso":        False,
            "mensagem":       "",
            "cidade_aplicada":"",
            "bairro_aplicado":"",
            "status_erro":    "",
            "mercadoi_url":   "",
        }

        fonte     = dados.get("_fonte", "")
        tem_preco = bool(dados.get("preco", "").strip())
        tem_tipo  = bool(dados.get("tipo_imovel", "").strip())
        forcar_rascunho = bool(dados.get("_forcar_rascunho"))
        # Instagram: publica só com imagem + preço + tipo
        # OLX/Órulo: publica com preço + tipo mesmo sem imagens
        publicar_direto = (not forcar_rascunho) and tem_preco and tem_tipo and (
            tipo_midia == "imagem" or fonte in ("olx", "orulo")
        )

        post_id, admin_url, public_url, err = await self._criar_imovel(dados, publicar=publicar_direto)
        if err:
            resultado["status_erro"] = "erro_preenchimento"
            resultado["mensagem"]    = err
            return resultado

        logger.info(f"[{self.execution_id}] Imóvel criado: id={post_id} url={admin_url}")

        if arquivo_midia:
            validos = [f for f in arquivo_midia if os.path.exists(f)]
            if validos:
                ok, enviados, erros = await self._subir_imagens(post_id, validos)
                if not ok and not enviados:
                    resultado["status_erro"] = "erro_upload"
                    resultado["mensagem"]    = f"Upload falhou: {'; '.join(erros)}"
                    return resultado
                if erros:
                    logger.warning(f"[{self.execution_id}] Erros parciais no upload: {erros}")
                logger.info(f"[{self.execution_id}] {enviados} imagem(ns) enviada(s)")
            else:
                logger.warning(f"[{self.execution_id}] Nenhum arquivo de mídia válido encontrado")

        resultado["sucesso"]         = True
        resultado["mercadoi_url"]    = admin_url
        resultado["cidade_aplicada"] = dados.get("cidade_extraida", "")
        resultado["bairro_aplicado"] = dados.get("bairro_extraido", "")
        resultado["mensagem"]        = "Publicado" if publicar_direto else "Rascunho salvo"
        resultado["url_publica"]     = public_url
        return resultado

    # ------------------------------------------------------------------
    # POST /properties
    # ------------------------------------------------------------------

    async def _criar_imovel(self, dados: dict, publicar: bool) -> tuple[int | None, str, str, str]:
        def _s(key: str) -> str:
            v = dados.get(key, "")
            return v.strip() if isinstance(v, str) else str(v or "").strip()

        tipo_lista = dados.get("tipo_imovel_lista") or []
        if isinstance(tipo_lista, str):
            tipo_lista = [p.strip() for p in tipo_lista.split(",") if p.strip()]

        payload = {
            "titulo":          _s("titulo"),
            "descricao":       _s("descricao_util"),
            "tipo_imovel":     _s("tipo_imovel"),
            "tipo_imovel_lista": tipo_lista,
            "operacao":        _s("operacao") or "A Venda",
            "preco":           _s("preco"),
            "condominio":      _s("condominio"),
            "iptu":            _s("iptu"),
            "taxas":           _s("taxas"),
            "quartos":         _s("quartos"),
            "suites":          _s("suites"),
            "banheiros":       _s("banheiros"),
            "vagas":           _s("vagas"),
            "area_m2":         _s("area_m2"),
            "area_terreno":    _s("area_terreno"),
            "ano_construcao":  _s("ano_construcao"),
            "estagio_imovel":  _s("estagio_imovel"),
            "andar":           _s("andar"),
            "elevador":        _s("elevador"),
            "posicao_solar":   _s("posicao_solar"),
            "mobiliado":       _s("mobiliado"),
            "escriturado":     _s("escriturado"),
            "aceita_airbnb":   _s("aceita_airbnb"),
            "aceita_financiamento": _s("aceita_financiamento"),
            "proximidades":    _s("proximidades"),
            "cidade":          _s("cidade_extraida"),
            "bairro":          _s("bairro_extraido"),
            "endereco":        _s("endereco"),
            "url_publicacao":  _s("url_publicacao"),
            "whatsapp_url":    _s("whatsapp_url"),
            "instagram_url":   _s("instagram_url"),
            "caracteristicas": dados.get("caracteristicas") or [],
            "publicar":        publicar,
            "origem":          dados.get("_fonte", ""),
        }
        if dados.get("_fonte") == "orulo":
            payload["mercadoi_agent_name"] = "Agustin Machado"

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{self.api_url}/properties",
                    json=payload,
                    headers=self._headers,
                )
            if r.status_code not in (200, 201):
                msg = self._extract_error(r)
                logger.error(f"[{self.execution_id}] Criar imóvel falhou ({r.status_code}): {msg}")
                return None, "", "", msg
            data = r.json()
            return data.get("id"), data.get("url_admin", ""), data.get("url_publica", ""), ""
        except httpx.TimeoutException:
            return None, "", "", "Timeout ao criar imóvel na API WordPress"
        except Exception as e:
            return None, "", "", f"Erro ao criar imóvel: {e}"

    # ------------------------------------------------------------------
    # POST /properties/{id}/media
    # ------------------------------------------------------------------

    async def _subir_imagens(self, post_id: int, caminhos: list) -> tuple[bool, int, list]:
        files   = []
        handles = []
        try:
            for caminho in caminhos:
                fh = open(caminho, "rb")
                handles.append(fh)
                files.append(("files[]", (os.path.basename(caminho), fh, "image/jpeg")))

            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                r = await client.post(
                    f"{self.api_url}/properties/{post_id}/media",
                    files=files,
                    headers=self._headers,
                )
        finally:
            for fh in handles:
                try: fh.close()
                except Exception: pass

        if r.status_code not in (200, 201):
            msg = self._extract_error(r)
            logger.error(f"[{self.execution_id}] Upload falhou ({r.status_code}): {msg}")
            return False, 0, [msg]

        data    = r.json()
        enviados = data.get("sucesso", 0)
        erros    = data.get("erros", [])
        return True, enviados, erros

    # ------------------------------------------------------------------
    # GET /options
    # ------------------------------------------------------------------

    async def buscar_opcoes(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{self.api_url}/options", headers=self._headers)
            return r.json() if r.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Erro ao buscar opções da API: {e}")
            return {}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        try:
            data = response.json()
            return data.get("message") or data.get("mensagem") or str(data)
        except Exception:
            return response.text[:200]
