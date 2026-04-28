"""
Publica imoveis no WordPress via REST API.
Substitui o MercadoiDriver (Playwright) na Fase 1 do plano v4.0.
"""

import os
import asyncio
import httpx
from modules.logger import Logger

logger = Logger("wordpress_publisher")

TIMEOUT = 60          # segundos por request
UPLOAD_TIMEOUT = 120  # upload de imagens pode demorar mais


class WordPressPublisher:
    def __init__(self, api_url: str, api_key: str, execution_id: str = ""):
        # api_url: https://site.com.br/wp-json/bot-mercadoi/v1
        self.api_url      = api_url.rstrip("/")
        self.api_key      = api_key
        self.execution_id = execution_id
        self._headers     = {"Authorization": f"Bearer {api_key}"}

    # ------------------------------------------------------------------
    # Interface pública — mesma assinatura que MercadoiDriver
    # ------------------------------------------------------------------

    async def preencher_e_salvar(self, dados: dict, tipo_midia: str, arquivo_midia: list) -> dict:
        resultado = {
            "sucesso":        False,
            "mensagem":       "",
            "cidade_aplicada":"",
            "bairro_aplicado":"",
            "status_erro":    "",
            "mercadoi_url":   "",
        }

        # 1. Decide se publica ou salva como rascunho (mesma lógica do driver)
        publicar_direto = (
            tipo_midia == "imagem"
            and bool(dados.get("preco", "").strip())
            and bool(dados.get("tipo_imovel", "").strip())
        )

        # 2. Cria o imóvel
        post_id, admin_url, public_url, err = await self._criar_imovel(dados, publicar=publicar_direto)
        if err:
            resultado["status_erro"] = "erro_preenchimento"
            resultado["mensagem"]    = err
            return resultado

        logger.info(f"[{self.execution_id}] Imóvel criado: id={post_id} url={admin_url}")

        # 3. Sobe imagens
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
        """Retorna (post_id, url_admin, url_publica, erro)."""
        def _s(key: str) -> str:
            v = dados.get(key, "")
            return v.strip() if isinstance(v, str) else str(v or "").strip()

        payload = {
            # Obrigatório
            "titulo":          _s("titulo"),
            # Descrição
            "descricao":       _s("descricao_util"),
            # Operação e tipo
            "tipo_imovel":     _s("tipo_imovel"),
            "operacao":        _s("operacao") or "A Venda",
            # Valores financeiros
            "preco":           _s("preco"),
            "condominio":      _s("condominio"),
            # Características do imóvel
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
            # Localização (usada também para geocodificar no plugin)
            "cidade":          _s("cidade_extraida"),
            "bairro":          _s("bairro_extraido"),
            "endereco":        _s("endereco"),
            # Links de contato (vão para o conteúdo do post)
            "url_publicacao":  _s("url_publicacao"),
            "whatsapp_url":    _s("whatsapp_url"),
            "instagram_url":   _s("instagram_url"),
            # Características (array de strings)
            "caracteristicas": dados.get("caracteristicas") or [],
            # Controle
            "publicar":        publicar,
        }

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
            return (
                data.get("id"),
                data.get("url_admin", ""),
                data.get("url_publica", ""),
                "",
            )
        except httpx.TimeoutException:
            return None, "", "", "Timeout ao criar imóvel na API WordPress"
        except Exception as e:
            return None, "", "", f"Erro ao criar imóvel: {e}"

    # ------------------------------------------------------------------
    # POST /properties/{id}/media
    # ------------------------------------------------------------------

    async def _subir_imagens(self, post_id: int, caminhos: list) -> tuple[bool, int, list]:
        """Retorna (ok, quantidade_enviada, lista_de_erros)."""
        files = []
        handles = []
        try:
            for caminho in caminhos:
                fh = open(caminho, "rb")
                handles.append(fh)
                nome = os.path.basename(caminho)
                files.append(("files[]", (nome, fh, "image/jpeg")))

            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                r = await client.post(
                    f"{self.api_url}/properties/{post_id}/media",
                    files=files,
                    headers=self._headers,
                )
        finally:
            for fh in handles:
                try:
                    fh.close()
                except Exception:
                    pass

        if r.status_code not in (200, 201):
            msg = self._extract_error(r)
            logger.error(f"[{self.execution_id}] Upload falhou ({r.status_code}): {msg}")
            return False, 0, [msg]

        data    = r.json()
        enviados = data.get("sucesso", 0)
        erros    = data.get("erros", [])
        return True, enviados, erros

    # ------------------------------------------------------------------
    # GET /options  (utilitário para validação e debug)
    # ------------------------------------------------------------------

    async def buscar_opcoes(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{self.api_url}/options",
                    headers=self._headers,
                )
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
