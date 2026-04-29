"""
Publica imóveis no WordPress via XML-RPC.
Não requer plugin, functions.php ou Application Passwords.
Só precisa de usuário e senha WordPress com permissão para publicar.
XML-RPC está ativo por padrão em todos os WordPress modernos.
"""

import os
import asyncio
import xmlrpc.client
import httpx
from urllib.parse import urlparse
from modules.logger import Logger

logger = Logger("wordpress_xmlrpc_publisher")

GEOCODE_TIMEOUT = 8


class WordPressXmlRpcPublisher:
    def __init__(self, site_url: str, wp_user: str, wp_password: str, execution_id: str = ""):
        self._site_url   = site_url.rstrip("/")
        self._xmlrpc_url = self._site_url + "/xmlrpc.php"
        self._user       = wp_user
        self._pass       = wp_password
        self.execution_id = execution_id

    def _proxy(self) -> xmlrpc.client.ServerProxy:
        # Novo proxy por chamada — thread-safe para uso com run_in_executor
        return xmlrpc.client.ServerProxy(self._xmlrpc_url, allow_none=True)

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

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

        publicar_direto = (
            tipo_midia == "imagem"
            and bool(dados.get("preco", "").strip())
            and bool(dados.get("tipo_imovel", "").strip())
        )

        post_id, admin_url, err = await self._criar_imovel(dados, publicar=publicar_direto)
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
        return resultado

    # ------------------------------------------------------------------
    # Criar imóvel via wp.newPost
    # ------------------------------------------------------------------

    async def _criar_imovel(self, dados: dict, publicar: bool) -> tuple[int | None, str, str]:
        def _s(key: str) -> str:
            v = dados.get(key, "")
            return v.strip() if isinstance(v, str) else str(v or "").strip()

        content       = _build_content(dados)
        custom_fields = _build_custom_fields(dados, content)

        terms_names: dict = {}
        if _s("tipo_imovel"):      terms_names["property-type"]    = [_s("tipo_imovel")]
        if _s("operacao"):         terms_names["property-status"]   = [_s("operacao") or "A Venda"]
        if _s("cidade_extraida"):  terms_names["property-city"]     = [_s("cidade_extraida")]
        if _s("bairro_extraido"):  terms_names["property-area"]     = [_s("bairro_extraido")]
        caracteristicas = dados.get("caracteristicas") or []
        if caracteristicas:        terms_names["property-feature"]  = list(caracteristicas)

        post_data = {
            "post_title":    _s("titulo"),
            "post_content":  content,
            "post_status":   "publish" if publicar else "draft",
            "post_type":     "property",
            "custom_fields": custom_fields,
            "terms_names":   terms_names,
        }

        try:
            post_id = await self._run(self._sync_new_post, post_data)
            post_id = int(post_id)
        except xmlrpc.client.Fault as e:
            fault_lower = e.faultString.lower()
            if "taxonom" in fault_lower or "term" in fault_lower:
                # Taxonomia inválida ou sem permissão — tenta sem terms_names
                logger.warning(f"[{self.execution_id}] Taxonomy rejeitada, tentando sem termos: {e.faultString}")
                post_data_sem_termos = {k: v for k, v in post_data.items() if k != "terms_names"}
                try:
                    post_id = await self._run(self._sync_new_post, post_data_sem_termos)
                    post_id = int(post_id)
                except xmlrpc.client.Fault as e2:
                    return None, "", f"WordPress recusou a criação: {e2.faultString}"
                except Exception as e2:
                    return None, "", f"Erro XML-RPC ao criar imóvel: {e2}"
            else:
                return None, "", f"WordPress recusou a criação: {e.faultString}"
        except Exception as e:
            return None, "", f"Erro XML-RPC ao criar imóvel: {e}"

        await self._geocode_and_save(post_id, dados)

        admin_url = f"{self._site_url}/wp-admin/post.php?post={post_id}&action=edit"
        return post_id, admin_url, ""

    def _sync_new_post(self, post_data: dict) -> str:
        return self._proxy().wp.newPost(0, self._user, self._pass, post_data)

    def _sync_edit_post(self, post_id: int, post_data: dict) -> bool:
        return self._proxy().wp.editPost(0, self._user, self._pass, post_id, post_data)

    # ------------------------------------------------------------------
    # Upload de imagens via wp.uploadFile
    # ------------------------------------------------------------------

    async def _subir_imagens(self, post_id: int, caminhos: list) -> tuple[bool, int, list]:
        uploaded_ids = []
        errors       = []

        for caminho in caminhos:
            try:
                att_id = await self._run(self._sync_upload_file, caminho, post_id)
                uploaded_ids.append(att_id)
            except Exception as e:
                errors.append(f"{os.path.basename(caminho)}: {e}")

        if uploaded_ids:
            edit_data = {
                "wp_post_thumbnail": str(uploaded_ids[0]),
                "custom_fields": [
                    {"key": "fave_property_images",
                     "value": ",".join(map(str, uploaded_ids))}
                ],
            }
            try:
                await self._run(self._sync_edit_post, post_id, edit_data)
            except Exception as e:
                logger.warning(f"[{self.execution_id}] Erro ao vincular imagens ao imóvel: {e}")

        return bool(uploaded_ids), len(uploaded_ids), errors

    def _sync_upload_file(self, caminho: str, post_id: int) -> int:
        with open(caminho, "rb") as fh:
            bits = fh.read()
        result = self._proxy().wp.uploadFile(0, self._user, self._pass, {
            "name":    os.path.basename(caminho),
            "type":    "image/jpeg",
            "bits":    xmlrpc.client.Binary(bits),
            "post_id": post_id,
        })
        return int(result["id"])

    # ------------------------------------------------------------------
    # Geocoding via Nominatim
    # ------------------------------------------------------------------

    async def _geocode_and_save(self, post_id: int, dados: dict) -> None:
        parts = [v for v in [
            dados.get("endereco", "").strip(),
            dados.get("bairro_extraido", "").strip(),
            dados.get("cidade_extraida", "").strip(),
            "Brasil",
        ] if v]
        if len(parts) < 2:
            return

        query = ", ".join(parts)
        try:
            async with httpx.AsyncClient(timeout=GEOCODE_TIMEOUT) as client:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "json", "limit": 1, "addressdetails": 0},
                    headers={
                        "User-Agent":       "BotMercadoiXmlRpc/1.0",
                        "Accept-Language":  "pt-BR,pt;q=0.9",
                    },
                )
            body = r.json()
            if not body:
                return
            lat = float(body[0]["lat"])
            lng = float(body[0]["lon"])
        except Exception:
            return

        try:
            await self._run(self._sync_edit_post, post_id, {
                "custom_fields": [
                    {"key": "fave_property_location",    "value": f"{lat},{lng}"},
                    {"key": "fave_property_map_address", "value": query},
                    {"key": "fave_property_map_zoom",    "value": "15"},
                ],
            })
        except Exception as e:
            logger.warning(f"[{self.execution_id}] Geocoding falhou ao salvar: {e}")


# ---------------------------------------------------------------------------
# Helpers (module-level para facilitar testes unitários)
# ---------------------------------------------------------------------------

def _build_content(dados: dict) -> str:
    def _s(key: str) -> str:
        v = dados.get(key, "")
        return v.strip() if isinstance(v, str) else ""

    desc      = _s("descricao_util")
    url_pub   = _normalize_url(_s("url_publicacao"))
    whatsapp  = _normalize_url(_s("whatsapp_url"))
    instagram = _normalize_url(_s("instagram_url"))

    icons = ""
    if url_pub:
        icons += f'<a href="{url_pub}" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/ver-video-mi/" width="120" height="120" /></a>'
    if whatsapp:
        icons += f'<a href="{whatsapp}" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/whatsapp-mi/" width="75" height="75" /></a>'
    if instagram:
        icons += f'<a href="{instagram}" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/instagram-mi/" width="75" height="75" /></a>'

    return desc + (f"\n\n<pre>{icons}</pre>" if icons else "")


def _normalize_url(v: str) -> str:
    if not v:
        return ""
    if v.startswith(("wa.me/", "instagram.com/")):
        v = "https://" + v
    parsed = urlparse(v)
    return v if parsed.scheme and parsed.netloc else ""


def _build_custom_fields(dados: dict, content: str) -> list:
    def _s(key: str) -> str:
        v = dados.get(key, "")
        return v.strip() if isinstance(v, str) else str(v or "").strip()

    fields = []

    for field, meta_key in [
        ("preco",          "fave_property_price"),
        ("quartos",        "fave_property_bedrooms"),
        ("suites",         "fave_property_rooms"),
        ("banheiros",      "fave_property_bathrooms"),
        ("vagas",          "fave_property_garage"),
        ("area_m2",        "fave_property_size"),
        ("area_terreno",   "fave_property_land"),
        ("ano_construcao", "fave_property_year"),
        ("condominio",     "fave_property_condominium"),
    ]:
        if _s(field):
            fields.append({"key": meta_key, "value": _s(field)})

    operacao = _s("operacao").lower()
    postfix  = "/mês" if any(x in operacao for x in ("aluguel", "locacao", "locação")) else ""
    fields.append({"key": "fave_property_price_postfix", "value": postfix})

    for field, meta_key in [
        ("estagio_imovel", "estagio-da-obra-imóvel"),
        ("andar",          "no-térreo"),
        ("elevador",       "tem-elevador"),
    ]:
        if _s(field):
            fields.append({"key": meta_key, "value": _s(field)})

    fields.extend([
        {"key": "faz-parceria",              "value": "A combinar"},
        {"key": "prop_des",                  "value": content},
        {"key": "fave_agent_display_option", "value": "2"},
    ])

    return fields
