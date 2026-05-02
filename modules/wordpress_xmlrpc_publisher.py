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
            "url_publica":    "",
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

        post_id, admin_url, url_publica, err = await self._criar_imovel(dados, publicar=publicar_direto)
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
                    # Sem permissão de upload — imóvel já criado, apenas avisa
                    logger.warning(f"[{self.execution_id}] Upload sem permissão (401) — imóvel criado sem imagens. Conceda 'upload_files' ao usuário WordPress.")
                elif erros:
                    logger.warning(f"[{self.execution_id}] Erros parciais no upload: {erros}")
                logger.info(f"[{self.execution_id}] {enviados} imagem(ns) enviada(s)")
            else:
                logger.warning(f"[{self.execution_id}] Nenhum arquivo de mídia válido encontrado")

        resultado["sucesso"]         = True
        resultado["mercadoi_url"]    = admin_url
        resultado["url_publica"]     = url_publica
        resultado["cidade_aplicada"] = dados.get("cidade_extraida", "")
        resultado["bairro_aplicado"] = dados.get("bairro_extraido", "")
        resultado["mensagem"]        = "Publicado" if publicar_direto else "Rascunho salvo"
        return resultado

    # ------------------------------------------------------------------
    # Criar imóvel via wp.newPost
    # ------------------------------------------------------------------

    async def _criar_imovel(self, dados: dict, publicar: bool) -> tuple[int | None, str, str, str]:
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
                    return None, "", "", f"WordPress recusou a criação: {e2.faultString}"
                except Exception as e2:
                    return None, "", "", f"Erro XML-RPC ao criar imóvel: {e2}"
            else:
                return None, "", "", f"WordPress recusou a criação: {e.faultString}"
        except Exception as e:
            return None, "", "", f"Erro XML-RPC ao criar imóvel: {e}"

        await self._geocode_and_save(post_id, dados)

        admin_url  = f"{self._site_url}/wp-admin/post.php?post={post_id}&action=edit"
        url_publica = ""
        try:
            info = await self._run(
                lambda pid=post_id: self._proxy().wp.getPost(0, self._user, self._pass, pid, ["link"])
            )
            url_publica = info.get("link", "")
        except Exception:
            pass
        return post_id, admin_url, url_publica, ""

    def _sync_new_post(self, post_data: dict) -> str:
        return self._proxy().wp.newPost(0, self._user, self._pass, post_data)

    def _sync_edit_post(self, post_id: int, post_data: dict) -> bool:
        return self._proxy().wp.editPost(0, self._user, self._pass, post_id, post_data)

    # ------------------------------------------------------------------
    # Upload de imagens
    # ------------------------------------------------------------------

    async def _subir_imagens(self, post_id: int, caminhos: list) -> tuple[bool, int, list]:
        uploaded_ids  = []
        errors        = []
        xmlrpc_falhos = []  # imagens que receberam 401 do XML-RPC

        # Fase 1: tenta XML-RPC para todas as imagens
        for caminho in caminhos:
            try:
                att_id = await self._run(self._sync_upload_file, caminho, post_id)
                uploaded_ids.append(int(att_id))
            except xmlrpc.client.Fault as e:
                if "401" in str(e.faultCode) or "permiss" in e.faultString.lower():
                    # Sem permissão via XML-RPC — acumula para fallback em lote
                    xmlrpc_falhos.append(caminho)
                else:
                    errors.append(f"{os.path.basename(caminho)}: {e.faultString}")
            except Exception as e:
                errors.append(f"{os.path.basename(caminho)}: {e}")

        # Fase 2: todos os falhos em UMA ÚNICA sessão admin HTTP
        # (login único + nonce único — evita re-autenticar a cada arquivo)
        if xmlrpc_falhos:
            logger.info(
                f"[{self.execution_id}] XML-RPC 401 — {len(xmlrpc_falhos)} imagem(ns) via Admin HTTP (sessão única)"
            )
            admin_ids, admin_errors = await self._upload_lote_admin_http(post_id, xmlrpc_falhos)
            uploaded_ids.extend(admin_ids)
            errors.extend(admin_errors)

        # Fase 3: vincula todas as imagens enviadas ao post
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

    async def _upload_lote_admin_http(
        self, post_id: int, caminhos: list
    ) -> tuple[list, list]:
        """
        Faz upload de várias imagens via WP Admin HTTP em UMA ÚNICA sessão
        (login + nonce feitos apenas uma vez). Evita re-autenticar a cada arquivo.
        Retorna (lista_de_att_ids, lista_de_erros).
        """
        import re as _re
        login_url  = f"{self._site_url}/wp-login.php"
        upload_url = f"{self._site_url}/wp-admin/async-upload.php"

        att_ids = []
        errors  = []

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=90,
                cookies={"wordpress_test_cookie": "WP Cookie check"},
            ) as client:
                # 1. Login
                await client.post(login_url, data={
                    "log":         self._user,
                    "pwd":         self._pass,
                    "wp-submit":   "Log In",
                    "redirect_to": "/wp-admin/",
                    "testcookie":  "1",
                })

                # Verifica login
                chk = await client.get(f"{self._site_url}/wp-admin/")
                if "wp-login.php" in str(chk.url):
                    logger.warning(f"[{self.execution_id}] Admin HTTP: login falhou")
                    return [], [f"{os.path.basename(c)}: login falhou" for c in caminhos]

                # 2. Captura nonce — tenta várias páginas até encontrar
                nonce_pages = [
                    f"{self._site_url}/wp-admin/media-new.php",
                    f"{self._site_url}/wp-admin/",
                    f"{self._site_url}/wp-admin/profile.php",
                    f"{self._site_url}/wp-admin/post.php?post={post_id}&action=edit",
                ]
                nonce_patterns = [
                    r'"_wpnonce"\s*:\s*"([a-f0-9]+)"',
                    r'name="_wpnonce"\s+value="([a-f0-9]+)"',
                    r'"nonce"\s*:\s*"([a-f0-9]{8,})"',
                    r'wpApiSettings[^}]*"nonce"\s*:\s*"([a-f0-9]+)"',
                    r'"wp_rest_nonce"\s*:\s*"([a-f0-9]+)"',
                ]
                nonce = None
                for nurl in nonce_pages:
                    page = await client.get(nurl)
                    for pat in nonce_patterns:
                        m = _re.search(pat, page.text)
                        if m:
                            nonce = m.group(1)
                            logger.info(
                                f"[{self.execution_id}] Admin HTTP: nonce obtido de {nurl.split('/')[-1]}"
                            )
                            break
                    if nonce:
                        break

                if not nonce:
                    logger.warning(f"[{self.execution_id}] Admin HTTP: nonce não encontrado")
                    return [], [f"{os.path.basename(c)}: nonce não encontrado" for c in caminhos]

                # 3. Upload de cada imagem reusando a mesma sessão e nonce
                for caminho in caminhos:
                    try:
                        with open(caminho, "rb") as fh:
                            img_bytes = fh.read()

                        resp = await client.post(
                            upload_url,
                            data={"_wpnonce": nonce, "action": "upload-attachment", "post_id": str(post_id)},
                            files={"async-upload": (os.path.basename(caminho), img_bytes, "image/jpeg")},
                        )

                        if resp.status_code == 200:
                            data = resp.json()
                            att_id = (data.get("data") or {}).get("id") or data.get("id")
                            if att_id:
                                logger.info(
                                    f"[{self.execution_id}] Admin HTTP: upload OK "
                                    f"{os.path.basename(caminho)} id={att_id}"
                                )
                                att_ids.append(int(att_id))
                                continue
                        errors.append(
                            f"{os.path.basename(caminho)}: HTTP {resp.status_code}"
                        )
                    except Exception as e:
                        errors.append(f"{os.path.basename(caminho)}: {e}")

        except Exception as e:
            logger.warning(f"[{self.execution_id}] Admin HTTP upload falhou: {e}")
            errors.extend([f"{os.path.basename(c)}: sessão falhou — {e}" for c in caminhos if not any(os.path.basename(c) in err for err in errors)])

        return att_ids, errors

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
