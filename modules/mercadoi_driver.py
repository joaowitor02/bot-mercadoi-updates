"""
Modulo de automacao do formulario do Mercadoi.
"""

import json
import os
import sys
import unicodedata
import re
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from modules.logger import Logger
from modules.property_types import aplicar_tipos_imovel, normalizar_tipo_imovel

logger = Logger("mercadoi_driver")


def normalizar(texto):
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.lower().strip())


# Características que NUNCA devem ser marcadas no formulário Mercadoi
# (independente da seção — comum ou privativa)
_NUNCA_MARCAR: set[str] = {normalizar(x) for x in [
    # Áreas Comuns — amenidades de uso coletivo do prédio
    "Academia",
    "Banheiro social",
    "Biblioteca",
    "Circuito de seguranca",
    "Camera de seguranca",
    "Espaco gourmet",
    "Lounge",
    "Portaria 24h",
    "Portaria eletronica",
    "Salao de festas / SUM",
    "Salao de festas",
    "Salao de jogos",
    # "Solarium" → OK marcar (confirmado nos screenshots do cliente)
    "Spa",
    "Terraco",
    "Terraco Rooftop",
    "Rooftop",
    "Sistema de alarme",
    # Áreas Privativas — nunca marcar automaticamente
    "Piscina infantil",
    "Piscina Privativa",
    "Espaco Gourmet",           # versão privativa igual à comum — ambas bloqueadas
]}


# Mapeamento bairro → cidade para municípios da Paraíba
_BAIRRO_CIDADE_PB: dict[str, str] = {
    # João Pessoa
    "manaira": "João Pessoa", "tambau": "João Pessoa", "cabo branco": "João Pessoa",
    "miramar": "João Pessoa", "bessa": "João Pessoa", "torre": "João Pessoa",
    "bancarios": "João Pessoa", "mangabeira": "João Pessoa", "valentina": "João Pessoa",
    "brisamar": "João Pessoa", "jardim oceania": "João Pessoa", "oceania": "João Pessoa",
    "estados": "João Pessoa", "bairro dos estados": "João Pessoa",
    "epitacio pessoa": "João Pessoa", "altiplano": "João Pessoa",
    "roger": "João Pessoa", "jaguaribe": "João Pessoa", "geisel": "João Pessoa",
    "cristo redentor": "João Pessoa", "castelo branco": "João Pessoa",
    "agua fria": "João Pessoa", "cruz das armas": "João Pessoa",
    "funcionarios": "João Pessoa", "expedicionarios": "João Pessoa",
    "tambia": "João Pessoa", "varadouro": "João Pessoa", "trincheiras": "João Pessoa",
    "pedro gondim": "João Pessoa", "aeroclube": "João Pessoa", "grotao": "João Pessoa",
    "cidade universitaria": "João Pessoa", "anatolia": "João Pessoa",
    "jose americo": "João Pessoa", "planalto": "João Pessoa",
    "cuia": "João Pessoa", "paratibe": "João Pessoa", "gramame": "João Pessoa",
    "mussumagro": "João Pessoa", "portal do sol": "João Pessoa",
    "costa e silva": "João Pessoa", "jose bezerra": "João Pessoa",
    "mandacaru": "João Pessoa", "san martin": "João Pessoa",
    "jardim luna": "João Pessoa", "alto do ceu": "João Pessoa",
    "penha": "João Pessoa", "ilha do bispo": "João Pessoa", "rangel": "João Pessoa",
    "13 de maio": "João Pessoa", "treze de maio": "João Pessoa",
    "jardim sao paulo": "João Pessoa", "padre zé": "João Pessoa", "padre ze": "João Pessoa",
    "conjunto ceará": "João Pessoa", "conjunto ceara": "João Pessoa",
    "Paulo VI": "João Pessoa", "paulo vi": "João Pessoa",
    "novo horizonte joao pessoa": "João Pessoa",
    # Cabedelo
    "poco": "Cabedelo", "poca": "Cabedelo", "bairro do poco": "Cabedelo",
    "ponta de mato": "Cabedelo", "renascer": "Cabedelo",
    "ponta de cabedelo": "Cabedelo", "intermares": "Cabedelo",
    "jardins cabedelo": "Cabedelo", "camalau": "Cabedelo",
    "centro cabedelo": "Cabedelo",
    # Campina Grande
    "bodocongo": "Campina Grande", "jose pinheiro": "Campina Grande",
    "dinamarca": "Campina Grande", "liberdade": "Campina Grande",
    "sandra cavalcante": "Campina Grande", "malvinas": "Campina Grande",
    "catole": "Campina Grande", "prata": "Campina Grande",
    "bela vista campina": "Campina Grande", "palmeira campina": "Campina Grande",
    "universitario campina": "Campina Grande", "miriam coelho": "Campina Grande",
    "serrotao": "Campina Grande", "monte castelo": "Campina Grande",
    "centenario": "Campina Grande", "itararé": "Campina Grande", "itarare": "Campina Grande",
    # Bayeux
    "bayeux": "Bayeux", "miramar bayeux": "Bayeux",
    # Santa Rita
    "santa rita": "Santa Rita", "várzea nova": "Santa Rita", "varzea nova": "Santa Rita",
    # Conde
    "jacuma": "Conde", "tabatinga": "Conde", "coqueirinho": "Conde",
    "barra de camaratuba": "Conde", "jacumã": "Conde",
    # Lucena
    "lucena": "Lucena", "fagundes": "Lucena",
    # Pitimbu
    "pitimbu": "Pitimbu", "acaua": "Pitimbu", "praia de pitimbu": "Pitimbu",
    # Alhandra
    "alhandra": "Alhandra",
    # Mamanguape
    "mamanguape": "Mamanguape",
    # Rio Tinto
    "rio tinto": "Rio Tinto",
    # Sapé
    "sape": "Sapé",
    # Guarabira
    "guarabira": "Guarabira",
    # Patos
    "patos": "Patos",
    # Sousa
    "sousa": "Sousa",
    # Cajazeiras
    "cajazeiras": "Cajazeiras",
}


class MercadoiDriver:
    def __init__(self, base_url, profile_path=r"C:\chrome_bot_mercadoi", execution_id: str = "",
                 wp_user: str = "", wp_pass: str = ""):
        self.base_url = base_url
        self.profile_path = self._normalizar_profile_path(profile_path)
        self.execution_id = execution_id
        self._wp_user = wp_user
        self._wp_pass = wp_pass
        data_dir = os.environ.get("BOT_DATA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._cookies_file = os.path.join(data_dir, "mercadoi_session.json")
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @staticmethod
    def _normalizar_profile_path(profile_path: str) -> str:
        if sys.platform == "win32":
            return profile_path

        data_dir = os.environ.get("BOT_DATA_DIR", "/data")
        if not profile_path or re.match(r"^[a-zA-Z]:\\", profile_path):
            return os.path.join(data_dir, "mercadoi_profile")
        return profile_path

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        if sys.platform == "win32":
            # Windows: conecta ao Chrome já aberto pelo usuário (porta 9222)
            self._browser = await self._playwright.chromium.connect_over_cdp("http://localhost:9222")
            contexts = self._browser.contexts
            if not contexts:
                raise Exception("Chrome do Mercadoi conectado, mas sem contexto disponivel")
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            logger.info("Conectado ao Chrome do Mercadoi ja aberto na porta 9222")
        else:
            # Linux/VPS: lança Chromium headless
            os.makedirs(self.profile_path, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_path,
                headless=True,
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            logger.info(f"Browser headless persistente iniciado para Mercadoi (perfil: {self.profile_path})")
        return self

    async def __aexit__(self, *args):
        if sys.platform != "win32":
            # VPS: fecha o browser após cada uso
            try:
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()

    _SKIP_COOKIES = {"wordpress_test_cookie"}

    async def _garantir_login(self, page):
        await page.goto(f"{self.base_url}/create-a-listing/", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        if await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu, #prop_title'):
            return

        if sys.platform != "win32":
            # Tenta cookies salvos independente de ter credenciais
            if await self._carregar_sessao(page):
                return
            # Login automático se credenciais estiverem configuradas
            if self._wp_user and self._wp_pass:
                try:
                    await self._fazer_login_httpx(page)
                except Exception as e:
                    logger.warning(f"Login via httpx falhou, tentando formulario do wp-login.php: {e}")
                    await self._fazer_login_formulario(page)
                return
            raise Exception(
                "Mercadoi nao autenticado no VPS. Opcoes: "
                "(1) configure wordpress_xmlrpc_user/password para login automatico, ou "
                "(2) exporte a sessao do Chrome Windows pelo painel e transfira mercadoi_session.json para /data/."
            )

        logger.info("Aguardando login manual ate 120s...")
        await page.goto(f"{self.base_url}/login/", timeout=30000)
        for _ in range(60):
            await page.wait_for_timeout(2000)
            if await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu'):
                logger.info("Login detectado, continuando...")
                return
        raise Exception("Timeout aguardando login no Mercadoi")

    async def _carregar_sessao(self, page) -> bool:
        if not os.path.exists(self._cookies_file):
            return False
        try:
            with open(self._cookies_file, encoding="utf-8") as f:
                cookies = json.load(f)
            filtrados = [c for c in cookies if c.get("name") not in self._SKIP_COOKIES]
            await self._context.clear_cookies()
            if filtrados:
                await self._context.add_cookies(filtrados)
            await page.goto(f"{self.base_url}/create-a-listing/", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            if await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu, #prop_title'):
                logger.info("Sessao restaurada do arquivo de cookies")
                return True
            logger.info("Cookies expirados, fazendo novo login")
        except Exception as e:
            logger.warning(f"Erro ao carregar sessao: {e}")
        return False

    async def _fazer_login_httpx(self, page):
        import httpx
        from urllib.parse import urlparse
        domain = urlparse(self.base_url).netloc
        logger.info("Fazendo login via httpx...")
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            await client.get(f"{self.base_url}/wp-login.php")
            await client.post(
                f"{self.base_url}/wp-login.php",
                data={
                    "log": self._wp_user,
                    "pwd": self._wp_pass,
                    "wp-submit": "Log In",
                    "redirect_to": f"{self.base_url}/create-a-listing/",
                    "testcookie": "1",
                },
                headers={"Cookie": "wordpress_test_cookie=WP Cookie check"},
            )
        seen: set = set()
        playwright_cookies = []
        for k, v in client.cookies.items():
            if k in self._SKIP_COOKIES or k in seen:
                continue
            seen.add(k)
            playwright_cookies.append({"name": k, "value": v, "domain": domain, "path": "/"})
        auth = [c for c in playwright_cookies if "wordpress_logged_in" in c["name"]]
        if not auth:
            raise Exception(f"Login httpx falhou — sem cookie auth. Obtidos: {list(seen)}")
        logger.info(f"Cookie auth obtido: {[c['name'] for c in auth]}")
        await self._context.clear_cookies()
        await self._context.add_cookies(playwright_cookies)
        await page.goto(f"{self.base_url}/create-a-listing/", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        if not await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu, #prop_title'):
            raise Exception("Login httpx: sessao nao confirmada apos injecao de cookies")
        try:
            save = [c for c in await self._context.cookies() if c["name"] not in self._SKIP_COOKIES]
            os.makedirs(os.path.dirname(self._cookies_file), exist_ok=True)
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(save, f)
            logger.info(f"Sessao salva ({len(save)} cookies) em {self._cookies_file}")
        except Exception as e:
            logger.warning(f"Erro ao salvar sessao: {e}")
        logger.info("Login realizado com sucesso")

    async def _fazer_login_formulario(self, page):
        logger.info("Fazendo login pelo formulario wp-login.php...")
        await page.goto(f"{self.base_url}/wp-login.php", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.fill("#user_login", self._wp_user)
        await page.fill("#user_pass", self._wp_pass)
        try:
            await page.check("#rememberme", timeout=2000)
        except Exception:
            pass
        await page.click("#wp-submit")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        await page.goto(f"{self.base_url}/create-a-listing/", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        if not await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu, #prop_title'):
            raise Exception("Login via formulario: sessao nao confirmada")

        try:
            save = [c for c in await self._context.cookies() if c["name"] not in self._SKIP_COOKIES]
            os.makedirs(os.path.dirname(self._cookies_file), exist_ok=True)
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(save, f)
            logger.info(f"Sessao salva ({len(save)} cookies) em {self._cookies_file}")
        except Exception as e:
            logger.warning(f"Erro ao salvar sessao: {e}")
        logger.info("Login via formulario realizado com sucesso")

    async def preencher_e_salvar(self, dados, tipo_midia, arquivo_midia):
        dados = aplicar_tipos_imovel(dict(dados or {}))
        page = self._page
        await self._garantir_login(page)
        resultado = {
            "sucesso": False,
            "mensagem": "",
            "cidade_aplicada": "",
            "bairro_aplicado": "",
            "status_erro": "",
            "mercadoi_url": "",
        }

        try:
            # Navega apenas se não estiver já na página de cadastro
            listing_url = f"{self.base_url}/create-a-listing/"
            if listing_url.rstrip("/") not in page.url:
                await page.goto(listing_url, timeout=30000)
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector('#prop_title', timeout=10000)
            except Exception:
                logger.warning("Campo #prop_title demorou para aparecer, continuando mesmo assim")

            # TITULO
            titulo = dados.get("titulo", "").strip()
            if not titulo:
                resultado["status_erro"] = "erro_preenchimento"
                resultado["mensagem"] = "Titulo ausente"
                return resultado
            await page.fill('#prop_title', titulo)
            logger.info(f"Titulo preenchido: {titulo}")

            # CONTEUDO
            conteudo = self._montar_conteudo(dados)
            await self._preencher_editor(page, conteudo)

            # TIPO DE IMOVEL
            tipos_imovel = dados.get("tipo_imovel_lista") or []
            if isinstance(tipos_imovel, str):
                tipos_imovel = [p.strip() for p in tipos_imovel.split(",") if p.strip()]
            tipos_imovel = [self._normalizar_tipo_imovel(t) for t in tipos_imovel] or [
                self._normalizar_tipo_imovel(dados.get("tipo_imovel", ""))
            ]
            await self._selecionar_tipos_imovel(page, tipos_imovel)

            # OPERACAO
            operacao = dados.get("operacao", "").strip() or "A Venda"
            await self._selecionar_por_texto(page, '#prop_status', operacao)

            # Preenche todos os campos numéricos em uma única chamada JS.
            # Mercadoi/Houzez usa #prop_baths para banheiros e #prop_rooms para suites.
            await self._preencher_campos_batch(page, {
                '#prop_price':  dados.get("preco",    "").strip(),
                '#prop_beds':   dados.get("quartos",  "").strip(),
                '#prop_baths':  dados.get("banheiros","").strip(),
                '#prop_rooms':  dados.get("suites",   "").strip(),
                '#prop_garage': dados.get("vagas",    "").strip(),
                '#prop_size':   dados.get("area_m2",  "").strip(),
            })

            # CARACTERISTICAS
            await self._marcar_caracteristicas(page, dados.get("caracteristicas") or [])

            # Seleciona todos os campos de detalhe com seletores CSS exatos do formulário Mercadoi
            det = self._detalhes_adicionais(dados)
            dv  = det["selects"]
            await self._selecionar_batch(page, [
                # Campos simples (sem [])
                ('select[name="tem-elevador"]',              dv.get("Tem elevador?", "")),
                ('select[name="mobiliado"]',                 dv.get("Mobiliado?", "")),
                ('select[name="escriturado"]',               dv.get("Escriturado?", "")),
                ('select[name="aceita-airbnb-temporada"]',   dv.get("Aceita Airbnb/Temporada", "")),
                ('select[name="aceita-permuta"]',            dv.get("Aceita Permuta?", "")),
                ('select[name="aceita-financiamento"]',      dv.get("Aceita Financiamento?", "")),
                ('select[name="posic3a7c3a3o-do-imovel"]',  dv.get("Posição Solar", "")),
                ('select[name="posic3a7c3a3o"]',             dv.get("Posição no Prédio", "")),
                # Campos com [] (multiple-select com selectpicker)
                ('select[name="estagio-da-obra-imc3b3vel[]"]', dv.get("Estágio do Imovel", "")),
                ('select[name="no-tc3a9rreo[]"]',           dv.get("Andar", "")),
                ('select[name="perto-do-mar[]"]',            dv.get("Perto do mar?", "")),
            ])

            # Campos de texto livre (área, condomínio, ano, proximidades)
            await self._preencher_detalhes_adicionais(page, dados)

            # Faz Parceria: Orulo → 50/50 | OLX/Instagram → A combinar
            _fonte_parceria = dados.get("_fonte", "")
            _labels_parceria = (
                ("50/50", "50% / 50%", "50% /50%", "50%/50%")
                if _fonte_parceria == "orulo"
                else ("A combinar",)
            )
            _parceria_ok = False
            for _label in _labels_parceria:
                try:
                    await page.select_option('select[name*="parcer"]', label=_label, timeout=2000)
                    logger.info(f"Selecionado '{_label}' em faz-parceria")
                    _parceria_ok = True
                    break
                except Exception:
                    pass
            if not _parceria_ok:
                try:
                    opcoes = await page.evaluate("""
                        () => {
                            const sel = document.querySelector('select[name*="parcer"]');
                            return sel ? Array.from(sel.options).map(o => o.text.trim()) : [];
                        }
                    """)
                    logger.info(f"Faz-parceria: opcoes disponiveis = {opcoes}")
                except Exception:
                    logger.info("Faz-parceria: nao foi possivel selecionar")

            # CIDADE
            cidade = dados.get("cidade_extraida", "").strip()
            bairro = dados.get("bairro_extraido", "").strip()
            cidade_aplicada = await self._selecionar_cidade(page, cidade, bairro)
            resultado["cidade_aplicada"] = cidade_aplicada

            # BAIRRO
            bairro_aplicado = await self._selecionar_bairro(page, bairro)
            resultado["bairro_aplicado"] = bairro_aplicado

            # MIDIA
            if arquivo_midia:
                validos = [f for f in arquivo_midia if os.path.exists(f)]
                if validos:
                    upload_ok = await self._anexar_midia(page, validos)
                    if not upload_ok:
                        resultado["status_erro"] = "erro_upload"
                        resultado["mensagem"] = "Falha ao anexar midia no Mercadoi"
                        resultado["screenshot_path"] = await self._tirar_screenshot("erro_upload")
                        return resultado
                    if tipo_midia == "video":
                        logger.info(f"Video: {len(validos)} frame(s) extraido(s) anexados")
                else:
                    logger.warning("Nenhum arquivo de midia valido encontrado")
                    resultado["status_erro"] = "erro_upload"
                    resultado["mensagem"] = "Nenhum arquivo de midia valido encontrado"
                    return resultado

            if dados.get("_fonte") in ("orulo", "olx", "instagram"):
                # Preenche endereco/mapa quando a fonte trouxe rua ou coordenadas.
                await self._preencher_endereco_mapa(page, dados)

            if dados.get("_fonte") == "orulo":
                await self._selecionar_contato_corretor(
                    page,
                    "Agustin Machado",
                    dados.get("_mercadoi_agent_id", ""),
                )
            else:
                await self._marcar_nao_exibir_contato(page)

            # DECIDE: publicar direto ou salvar rascunho
            publicar_direto = (
                not bool(dados.get("_forcar_rascunho"))
                and dados.get("_fonte") != "olx"
                and tipo_midia == "imagem"
                and bool(dados.get("preco", "").strip())
                and bool(dados.get("tipo_imovel", "").strip())
            )

            agent_id_post = dados.get("_mercadoi_agent_id", "") if dados.get("_fonte") == "orulo" else ""

            # Campos de detalhe injetados diretamente no AJAX (bypass do selectpicker)
            # O Houzez PHP processa estes como $_POST e serializa corretamente
            det2 = self._detalhes_adicionais(dados)
            dv2  = det2["selects"]
            iv2  = det2["inputs"]
            extra_campos = {k: v for k, v in {
                "tem-elevador":              dv2.get("Tem elevador?", ""),
                "mobiliado":                 dv2.get("Mobiliado?", ""),
                "escriturado":               dv2.get("Escriturado?", ""),
                "aceita-airbnb-temporada":   dv2.get("Aceita Airbnb/Temporada", ""),
                "aceita-permuta":            dv2.get("Aceita Permuta?", ""),
                "aceita-financiamento":      dv2.get("Aceita Financiamento?", ""),
                "posic3a7c3a3o-do-imovel":  dv2.get("Posição Solar", ""),
                "posic3a7c3a3o":             dv2.get("Posição no Prédio", ""),
                # Campos array (selectpicker múltiplo)
                "estagio-da-obra-imc3b3vel[]": dv2.get("Estágio do Imovel", ""),
                "no-tc3a9rreo[]":            dv2.get("Andar", ""),
                "perto-do-mar[]":            dv2.get("Perto do mar?", ""),
                # Campos de texto
                "prop_land_area":             iv2.get("Área total m²", ""),
                "condomc3adnio-iptu-taxas":   iv2.get("Condomínio - IPTU - Taxas", ""),
                "prop_year_built":            iv2.get("Ano de construção", ""),
                "vizinhanc3a7a":              iv2.get("Proximidades", ""),
            }.items() if v}
            estagio_extra = dv2.get("Estágio do Imovel", "")
            if estagio_extra:
                extra_campos.update({
                    "estagio-da-obra-imc3b3vel": estagio_extra,
                    "fave_estagio-da-obra-imc3b3vel": estagio_extra,
                    "fave_estagio-da-obra-imc3b3vel[]": estagio_extra,
                })

            logger.info(f"extra_campos para AJAX: { {k: v for k, v in extra_campos.items()} }")

            if publicar_direto:
                logger.info("Criterios atendidos — publicando diretamente")
                salvamento = await self._publicar(page, agent_id=agent_id_post, extra_campos=extra_campos)
                modo = "Publicado"
                if not salvamento.get("ok"):
                    logger.warning("Publicacao direta falhou — salvando como rascunho")
                    salvamento = await self._salvar_rascunho(page, agent_id=agent_id_post, extra_campos=extra_campos)
                    modo = "Rascunho salvo"
            else:
                motivos = []
                if tipo_midia != "imagem":
                    motivos.append(f"midia={tipo_midia}")
                if not dados.get("preco", "").strip():
                    motivos.append("sem preco")
                if not dados.get("tipo_imovel", "").strip():
                    motivos.append("sem tipo")
                logger.info(f"Salvando como rascunho ({', '.join(motivos)})")
                salvamento = await self._salvar_rascunho(page, agent_id=agent_id_post, extra_campos=extra_campos)
                modo = "Rascunho salvo"

            if not salvamento.get("ok"):
                resultado["status_erro"] = "erro_salvamento"
                resultado["mensagem"] = f"Falha ao {'publicar' if publicar_direto else 'salvar rascunho'}"
                resultado["screenshot_path"] = await self._tirar_screenshot("erro_salvamento")
                return resultado

            # Atualiza meta de detalhes diretamente via XML-RPC (contorna limitações do form serialize)
            property_id = salvamento.get("property_id")
            if property_id:
                self._atualizar_meta_detalhes(property_id, dados)

            resultado["sucesso"] = True
            resultado["mensagem"] = f"{modo} com sucesso"
            resultado["mercadoi_url"] = salvamento.get("url", "")
            return resultado

        except Exception as e:
            logger.error(f"Erro no driver Mercadoi: {e}")
            resultado["status_erro"] = "erro_preenchimento"
            resultado["mensagem"] = str(e)
            resultado["screenshot_path"] = await self._tirar_screenshot("erro_preenchimento")
            return resultado

    def _normalizar_tipo_imovel(self, valor: str) -> str:
        return normalizar_tipo_imovel(valor or "")
        texto = normalizar(valor or "")
        if "cobertura" in texto:
            return "Apto. Cobertura"
        if "duplex" in texto:
            return "Apto. Duplex"
        if "flat" in texto:
            return "Apto. Flat"
        if "garden" in texto:
            return "Apto. Garden"
        if "apart" in texto or "studio" in texto or "kitnet" in texto or "kit net" in texto:
            return "Apartamento"
        if "chacara" in texto:
            return "Chácara"
        if "fazenda" in texto:
            return "Fazenda"
        if "sitio" in texto:
            return "Sítio"
        if "casa" in texto or "resid" in texto or "sobrado" in texto:
            return "Casa"
        if "terreno" in texto or "lote" in texto:
            return "Terreno"
        if "sala" in texto or "comercial" in texto or "loja" in texto or "escritorio" in texto:
            return "Sala Comercial"
        return valor.strip() or "Apartamento"

    async def _tirar_screenshot(self, etapa: str) -> str:
        """Captura screenshot da página atual e salva em logs/screenshots/."""
        try:
            os.makedirs("logs/screenshots", exist_ok=True)
            prefixo = f"{self.execution_id}_" if self.execution_id else ""
            caminho = os.path.join("logs", "screenshots", f"{prefixo}{etapa}.png")
            await self._page.screenshot(path=caminho, full_page=True)
            logger.info(f"Screenshot salvo: {caminho}")
            return caminho
        except Exception as e:
            logger.warning(f"Não foi possível salvar screenshot: {e}")
            return ""

    async def _selecionar_por_texto(self, page, seletor, valor):
        """
        Seleciona uma opcao de <select> pelo texto, usando JavaScript direto.
        Funciona tanto para selects normais quanto para selects controlados por select2,
        pois manipula o valor no DOM e dispara os eventos necessarios.
        """
        try:
            valor_norm = normalizar(valor)
            resultado = await page.evaluate(r"""
                ({seletor, valorNorm}) => {
                    const sel = document.querySelector(seletor);
                    if (!sel) return { ok: false, motivo: 'elemento nao encontrado' };
                    let melhorOpcao = null;
                    let melhorTexto = '';
                    for (const op of sel.options) {
                        const textoNorm = op.text.toLowerCase()
                            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                            .replace(/[ \\t]+/g, ' ').trim();
                        if (!textoNorm) continue;
                        // Strip prefixo "N- " (ex: "6- Usado" → "usado") para matching robusto
                        const textoBase = textoNorm.replace(/^\d+\s*[-\u2013]\s*/, '');
                        const match = textoNorm === valorNorm || textoNorm.includes(valorNorm) || valorNorm.includes(textoNorm)
                                   || textoBase === valorNorm || textoBase.includes(valorNorm) || valorNorm.includes(textoBase);
                        if (match) {
                            if (!melhorOpcao || op.text.length < melhorOpcao.text.length) {
                                melhorOpcao = op;
                                melhorTexto = op.text;
                            }
                        }
                    }
                    if (!melhorOpcao) return { ok: false, motivo: 'valor nao encontrado nas opcoes' };
                    if (window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).val(melhorOpcao.value).trigger('change');
                    } else {
                        sel.value = melhorOpcao.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        sel.dispatchEvent(new Event('input',  { bubbles: true }));
                    }
                    return { ok: true, texto: melhorTexto };
                }
            """, {"seletor": seletor, "valorNorm": valor_norm})

            if resultado and resultado.get('ok'):
                logger.info(f"Selecionado '{resultado['texto']}' em {seletor}")
                return True
            else:
                motivo = resultado.get('motivo', 'desconhecido') if resultado else 'erro JS'
                logger.info(f"Nao selecionou '{valor}' em {seletor}: {motivo}")
                return False
        except Exception as e:
            logger.info(f"Erro ao selecionar '{valor}' em {seletor}: {e}")
            return False

    async def _selecionar_tipo_imovel(self, page, tipo_imovel):
        """
        Seleciona o tipo principal por value fixo quando conhecido.
        O Mercadoi usa categorias numericas e, em alguns carregamentos, o select2
        nao atualiza bem quando escolhemos apenas por texto.
        """
        mapa_values = {
            "Apartamento":   "16",
            "Casa":          "53",
            "Terreno":       "103",
            "Sala Comercial":"98",
            # Subtipos — sem ID fixo, usa matching por texto via _selecionar_subtipo_imovel
        }
        _subtipos_texto = {
            "Apto. Flat", "Apto. Duplex", "Apto. Cobertura", "Apto. Garden",
            "Chácara", "Fazenda", "Sítio",
        }
        if tipo_imovel == "Casa de Condomínio":
            if await self._selecionar_subtipo_imovel(page, tipo_imovel):
                return True
            logger.info("Subtipo 'Casa de Condomínio' nao encontrado; usando 'Casa'")
            return await self._selecionar_tipo_imovel(page, "Casa")
        if tipo_imovel in _subtipos_texto:
            return await self._selecionar_subtipo_imovel(page, tipo_imovel)
        value = mapa_values.get(tipo_imovel)
        if value:
            try:
                resultado = await page.evaluate("""
                    ({value}) => {
                        const sel = document.querySelector('#prop_type');
                        if (!sel) return {ok: false, motivo: 'elemento nao encontrado'};
                        const option = Array.from(sel.options).find(op => op.value === value);
                        if (!option) return {ok: false, motivo: 'value nao encontrado'};
                        sel.value = value;
                        sel.dispatchEvent(new Event('input', {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        if (window.jQuery) {
                            window.jQuery(sel).val(value).trigger('change');
                            window.jQuery(sel).trigger({
                                type: 'select2:select',
                                params: {data: {id: value, text: option.text}}
                            });
                        }
                        return {ok: true, texto: option.text};
                    }
                """, {"value": value})
                if resultado and resultado.get("ok"):
                    logger.info(f"Selecionado tipo de imovel '{resultado['texto']}' em #prop_type")
                    return True
                motivo = resultado.get("motivo", "desconhecido") if resultado else "erro JS"
                logger.warning(f"Nao selecionou tipo '{tipo_imovel}' por value: {motivo}")
            except Exception as e:
                logger.warning(f"Erro ao selecionar tipo '{tipo_imovel}' por value: {e}")

        return await self._selecionar_por_texto(page, '#prop_type', tipo_imovel)

    async def _selecionar_tipos_imovel(self, page, tipos_imovel: list) -> bool:
        tipos = list(dict.fromkeys([t for t in tipos_imovel if t]))
        if len(tipos) <= 1:
            return await self._selecionar_tipo_imovel(page, tipos[0] if tipos else "Apartamento")
        try:
            resultado = await page.evaluate("""
                ({tipos}) => {
                    const sel = document.querySelector('#prop_type');
                    if (!sel) return {ok: false, motivo: 'elemento nao encontrado'};
                    const norm = t => t.toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/[\\s.]+/g, ' ').trim()
                        .replace(/^[-\\s]+/, '');
                    const alvos = tipos.map(norm);
                    const matches = [];
                    for (const alvo of alvos) {
                        let melhor = null;
                        for (const op of sel.options) {
                            const t = norm(op.text);
                            if (t === alvo || t.includes(alvo) || alvo.includes(t)) {
                                melhor = op; break;
                            }
                        }
                        if (melhor && !matches.some(op => op.value === melhor.value)) {
                            matches.push(melhor);
                        }
                    }
                    if (!matches.length) return {ok: false, motivo: 'opcoes nao encontradas'};
                    const valores = matches.map(op => op.value);
                    if (sel.multiple) {
                        for (const op of sel.options) op.selected = valores.includes(op.value);
                    } else {
                        sel.value = valores[0];
                    }
                    sel.dispatchEvent(new Event('input', {bubbles: true}));
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    if (window.jQuery) {
                        window.jQuery(sel).val(sel.multiple ? valores : valores[0]).trigger('change');
                    }
                    return {ok: true, textos: matches.map(op => op.text), multiple: sel.multiple};
                }
            """, {"tipos": tipos})
            if resultado and resultado.get("ok"):
                logger.info(f"Selecionado(s) tipo(s) de imovel: {', '.join(resultado.get('textos', []))}")
                return True
            logger.info(f"Nao selecionou tipos {tipos}: {resultado}")
        except Exception as e:
            logger.warning(f"Erro ao selecionar multiplos tipos {tipos}: {e}")
        return await self._selecionar_tipo_imovel(page, tipos[0])

    async def _selecionar_subtipo_imovel(self, page, tipo_imovel: str) -> bool:
        """Seleciona subtipo de imóvel (Apto. Flat, Apto. Duplex, etc.) pelo texto visível."""
        tipo_norm = normalizar(tipo_imovel)
        try:
            resultado = await page.evaluate("""
                ({tipoNorm}) => {
                    const sel = document.querySelector('#prop_type');
                    if (!sel) return {ok: false, motivo: 'elemento nao encontrado'};
                    const norm = t => t.toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/[\\s.]+/g, ' ').trim()
                        .replace(/^[-\\s]+/, '');   // remove "- " do início
                    const alvo = norm(tipoNorm);
                    let melhor = null;
                    for (const op of sel.options) {
                        const t = norm(op.text);
                        if (t === alvo || t.includes(alvo) || alvo.includes(t)) {
                            melhor = op; break;
                        }
                    }
                    if (!melhor) return {ok: false, motivo: 'opcao nao encontrada: ' + tipoNorm};
                    if (window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).val([melhor.value]).trigger('change');
                        window.jQuery(sel).trigger({
                            type: 'select2:select',
                            params: {data: {id: melhor.value, text: melhor.text}}
                        });
                    } else {
                        for (const op of sel.options) op.selected = false;
                        melhor.selected = true;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    return {ok: true, texto: melhor.text};
                }
            """, {"tipoNorm": tipo_norm})
            if resultado and resultado.get("ok"):
                logger.info(f"Selecionado subtipo '{resultado['texto']}' em #prop_type")
                return True
            logger.info(f"Subtipo '{tipo_imovel}' nao encontrado: {resultado}")
            return False
        except Exception as e:
            logger.warning(f"Erro ao selecionar subtipo '{tipo_imovel}': {e}")
            return False

    async def _marcar_nao_exibir_contato(self, page):
        """
        Marca a opcao 'Nao exibir contato' via JavaScript.
        O Mercadoi pode renderizar radios/checkboxes com labels; preferimos o
        controle cujo texto visivel seja "Nao exibir".
        """
        try:
            marcado = await page.evaluate("""
                () => {
                    const norm = (t) => String(t || '').toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/\\s+/g, ' ').trim();
                    const principais = Array.from(document.querySelectorAll(
                        'input[name="fave_agent_display_option"], input[name*="agent_display"]'
                    ));
                    const inputs = principais.length ? principais : Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
                    if (!inputs.length) return false;
                    let alvo = null;
                    for (const input of inputs) {
                        const label = input.closest('label') || document.querySelector(`label[for="${input.id}"]`) || input.parentElement;
                        const texto = norm(label ? label.innerText || label.textContent : '');
                        if (texto.includes('nao exibir') || texto.includes('não exibir')) {
                            alvo = input;
                            break;
                        }
                    }
                    if (!alvo && principais.length) alvo = principais.find(r => String(r.value) === "2") || principais[principais.length - 1];
                    if (!alvo) return false;
                    const nome = alvo.name;
                    if (nome) {
                        inputs.filter(i => i.name === nome && i !== alvo).forEach(i => {
                            if (i.type === 'checkbox' || i.type === 'radio') i.checked = false;
                        });
                    }
                    alvo.checked = true;
                    alvo.click();
                    alvo.dispatchEvent(new Event('input', { bubbles: true }));
                    alvo.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            """)
            if marcado:
                logger.info("Marcado: Nao exibir contato (via JS)")
            else:
                logger.warning("Input 'nao exibir contato' nao encontrado na pagina")
        except Exception as e:
            logger.warning(f"Erro ao marcar nao exibir contato: {e}")

    async def _selecionar_contato_corretor(self, page, nome_corretor: str, agent_id: str = ""):
        """Marca contato do corretor e seleciona o corretor informado."""
        try:
            marcado = await page.evaluate("""
                () => {
                    const norm = (t) => String(t || '').toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/\\s+/g, ' ').trim();
                    const radios = Array.from(document.querySelectorAll('input[name="fave_agent_display_option"]'));
                    if (!radios.length) return false;
                    let alvo = null;
                    for (const r of radios) {
                        const label = r.closest('label') || document.querySelector(`label[for="${r.id}"]`) || r.parentElement;
                        const texto = norm(label ? label.innerText : '');
                        if (texto.includes('corretor') || texto.includes('agent')) {
                            alvo = r; break;
                        }
                    }
                    if (!alvo) {
                        alvo = radios.find(r => ['agent_info', 'agent', '1'].includes(String(r.value))) || radios[0];
                    }
                    alvo.checked = true;
                    alvo.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            """)
            if marcado:
                logger.info("Marcado: Informacoes do Corretor")
                await page.wait_for_timeout(500)
            else:
                logger.warning("Opcao de contato do corretor nao encontrada")

            selecionou = False
            if await self._selecionar_corretor_por_clique(page, nome_corretor):
                selecionou = True
            if agent_id and await self._selecionar_corretor_por_id(page, str(agent_id).strip()):
                selecionou = True
            if not selecionou and await self._selecionar_corretor_por_texto(page, nome_corretor):
                selecionou = True

            if await self._fixar_corretor_no_formulario(page, nome_corretor, str(agent_id or "").strip()):
                return
            if selecionou:
                logger.warning(f"Corretor '{nome_corretor}' apareceu no Select2, mas nao entrou no formulario")
                return
            logger.warning(f"Corretor '{nome_corretor}' nao encontrado na lista")
        except Exception as e:
            logger.warning(f"Erro ao selecionar corretor '{nome_corretor}': {e}")

    async def _selecionar_corretor_por_id(self, page, agent_id: str) -> bool:
        if not agent_id:
            return False
        selecionado = await page.evaluate("""
            ({agentId}) => {
                const selects = Array.from(document.querySelectorAll(
                    'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                ));
                const aplicar = (sel, opt) => {
                    if (sel.multiple) {
                        Array.from(sel.options).forEach(o => o.selected = false);
                        opt.selected = true;
                    } else {
                        sel.value = opt.value;
                        opt.selected = true;
                    }
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.jQuery) {
                        const val = sel.multiple ? [opt.value] : opt.value;
                        const jq = window.jQuery(sel);
                        jq.val(val).trigger('change');
                        jq.trigger({
                            type: 'select2:select',
                            params: { data: { id: opt.value, text: opt.text || opt.label || '' } }
                        });
                        if (jq.data('selectpicker')) jq.selectpicker('refresh');
                    }
                    return {ok: true, texto: opt.text || opt.label || '', value: opt.value};
                };
                for (const sel of selects) {
                    const opt = Array.from(sel.options || []).find(o => String(o.value) === String(agentId));
                    if (opt) return aplicar(sel, opt);
                }
                return {ok: false};
            }
        """, {"agentId": agent_id})
        if selecionado and selecionado.get("ok"):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            logger.info(f"Corretor selecionado por ID: {selecionado.get('texto') or agent_id}")
            return True
        return False

    async def _selecionar_corretor_por_clique(self, page, nome_corretor: str) -> bool:
        """Usa cliques reais no Select2 do campo de corretor."""
        try:
            aberto = await page.evaluate("""
                () => {
                    const norm = (t) => String(t || '').toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/\\s+/g, ' ').trim();
                    const selects = Array.from(document.querySelectorAll('select, input[type="hidden"]'))
                        .filter(el => /(fave[_-]?agents?|agent)/i.test(`${el.name || ''} ${el.id || ''}`));
                    const sel = selects.find(s => !s.disabled) || selects[0];
                    if (!sel) return false;
                    try { sel.scrollIntoView({block: 'center'}); } catch (_) {}

                    let container = null;
                    if (sel.id) {
                        container = document.querySelector(`#select2-${CSS.escape(sel.id)}-container`);
                        if (container) container = container.closest('.select2-container');
                    }
                    if (!container) {
                        container = sel.nextElementSibling && sel.nextElementSibling.classList.contains('select2-container')
                            ? sel.nextElementSibling
                            : null;
                    }
                    if (window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).select2('open');
                        return true;
                    }
                    const selection = container && container.querySelector('.select2-selection');
                    if (!selection) return false;
                    selection.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    selection.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    selection.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }
            """)
            if not aberto:
                return False

            await page.wait_for_timeout(500)
            busca = page.locator('.select2-container--open .select2-search__field').last
            if not await busca.count():
                busca = page.locator('.select2-search__field').last
            if await busca.count():
                await busca.fill(nome_corretor, timeout=3000)
                await page.wait_for_timeout(900)

            variantes = [
                nome_corretor,
                re.sub(r"^augustin", "agustin", nome_corretor, flags=re.IGNORECASE),
                re.sub(r"^agustin", "augustin", nome_corretor, flags=re.IGNORECASE),
            ]
            for nome in dict.fromkeys(v for v in variantes if v):
                opcao = page.locator(
                    '.select2-container--open .select2-results__option:not(.loading-results):not(.select2-results__message)'
                ).filter(has_text=re.compile(re.escape(nome), re.IGNORECASE)).first
                try:
                    if await opcao.count():
                        await opcao.click(timeout=3000)
                        await page.wait_for_timeout(600)
                        await page.keyboard.press("Escape")
                        if await self._corretor_selecionado(page, nome_corretor):
                            logger.info(f"Corretor selecionado por clique: {nome_corretor}")
                            return True
                except Exception:
                    continue

            if await busca.count():
                try:
                    await busca.press("Enter", timeout=2000)
                    await page.wait_for_timeout(600)
                    await page.keyboard.press("Escape")
                    if await self._corretor_selecionado(page, nome_corretor):
                        logger.info(f"Corretor selecionado por Enter: {nome_corretor}")
                        return True
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Clique no corretor falhou: {e}")
        return False

    async def _selecionar_corretor_por_texto(self, page, nome_corretor: str) -> bool:
        selecionado = await page.evaluate("""
            ({nome}) => {
                const norm = (t) => String(t || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ').trim();
                const alvos = Array.from(new Set([
                    norm(nome),
                    norm(String(nome || '').replace(/^augustin/i, 'agustin')),
                    norm(String(nome || '').replace(/^agustin/i, 'augustin'))
                ])).filter(Boolean);
                const bate = (texto) => {
                    const t = norm(texto);
                    return alvos.some(alvo => t === alvo || t.includes(alvo) || alvo.includes(t));
                };
                const aplicar = (sel, opt) => {
                    if (sel.multiple) {
                        Array.from(sel.options).forEach(o => o.selected = false);
                        opt.selected = true;
                    } else {
                        sel.value = opt.value;
                        opt.selected = true;
                    }
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.jQuery) {
                        const val = sel.multiple ? [opt.value] : opt.value;
                        const jq = window.jQuery(sel);
                        jq.val(val).trigger('change');
                        jq.trigger({
                            type: 'select2:select',
                            params: { data: { id: opt.value, text: opt.text } }
                        });
                        if (jq.data('selectpicker')) jq.selectpicker('refresh');
                    }
                    return { ok: true, texto: opt.text, value: opt.value };
                };
                const selects = Array.from(document.querySelectorAll(
                    'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                ));
                for (const sel of selects) {
                    const opt = Array.from(sel.options).find(o => {
                        return bate(o.text || o.label || '');
                    });
                    if (!opt) continue;
                    return aplicar(sel, opt);
                }
                return { ok: false };
            }
        """, {"nome": nome_corretor})
        if selecionado and selecionado.get("ok"):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            if await self._corretor_selecionado(page, nome_corretor):
                logger.info(f"Corretor selecionado: {selecionado.get('texto')}")
                return True

        try:
            aberto = await page.evaluate("""
                () => {
                    const selects = Array.from(document.querySelectorAll(
                        'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                    ));
                    const sel = selects.find(s => !s.disabled);
                    if (!sel) return false;
                    let container = null;
                    if (sel.id) {
                        container = document.querySelector(`#select2-${CSS.escape(sel.id)}-container`);
                        if (container) container = container.closest('.select2-container');
                    }
                    if (!container) {
                        container = sel.nextElementSibling && sel.nextElementSibling.classList.contains('select2-container')
                            ? sel.nextElementSibling
                            : null;
                    }
                    if (!container && window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).select2('open');
                        return true;
                    }
                    const selection = container && container.querySelector('.select2-selection');
                    if (!selection) return false;
                    selection.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    selection.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    selection.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }
            """)
            if aberto:
                await page.wait_for_timeout(400)
                busca = page.locator('.select2-container--open .select2-search__field').last
                if not await busca.count():
                    busca = page.locator('.select2-search__field').last
                if await busca.count():
                    await busca.fill(nome_corretor, timeout=3000)
                    await page.wait_for_timeout(900)

                clicado = await page.evaluate("""
                    ({nome}) => {
                        const norm = (t) => String(t || '').toLowerCase()
                            .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                            .replace(/\\s+/g, ' ').trim();
                        const alvos = Array.from(new Set([
                            norm(nome),
                            norm(String(nome || '').replace(/^augustin/i, 'agustin')),
                            norm(String(nome || '').replace(/^agustin/i, 'augustin'))
                        ])).filter(Boolean);
                        const bate = (texto) => {
                            const t = norm(texto);
                            return alvos.some(alvo => t === alvo || t.includes(alvo) || alvo.includes(t));
                        };
                        const opcoes = Array.from(document.querySelectorAll(
                            '.select2-results__option:not(.loading-results):not(.select2-results__message)'
                        ));
                        const opcao = opcoes.find(o => {
                            return bate(o.innerText || o.textContent || '');
                        });
                        if (!opcao) return false;
                        opcao.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        opcao.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        opcao.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        return true;
                    }
                """, {"nome": nome_corretor})
                if clicado:
                    await page.wait_for_timeout(600)
                    # Garante que o select real ficou com o valor mesmo se o Select2 mantiver o dropdown aberto.
                    reforcado = await page.evaluate("""
                        ({nome}) => {
                            const norm = (t) => String(t || '').toLowerCase()
                                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ').trim();
                            const alvos = Array.from(new Set([
                                norm(nome),
                                norm(String(nome || '').replace(/^augustin/i, 'agustin')),
                                norm(String(nome || '').replace(/^agustin/i, 'augustin'))
                            ])).filter(Boolean);
                            const bate = (texto) => {
                                const t = norm(texto);
                                return alvos.some(alvo => t === alvo || t.includes(alvo) || alvo.includes(t));
                            };
                            const selects = Array.from(document.querySelectorAll(
                                'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                            ));
                            for (const sel of selects) {
                                const opt = Array.from(sel.options).find(o => bate(o.text || o.label || ''));
                                if (!opt) continue;
                                const val = sel.multiple ? [opt.value] : opt.value;
                                opt.selected = true;
                                if (!sel.multiple) sel.value = opt.value;
                                sel.dispatchEvent(new Event('input', { bubbles: true }));
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                if (window.jQuery) window.jQuery(sel).val(val).trigger('change');
                                return true;
                            }
                            return false;
                        }
                    """, {"nome": nome_corretor})
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(300)
                    if reforcado and await self._corretor_selecionado(page, nome_corretor):
                        logger.info(f"Corretor selecionado via Select2: {nome_corretor}")
                        return True
                try:
                    await busca.press("Enter", timeout=2000)
                    await page.wait_for_timeout(500)
                    await page.keyboard.press("Escape")
                    if await self._corretor_selecionado(page, nome_corretor):
                        logger.info(f"Corretor selecionado via Enter: {nome_corretor}")
                        return True
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Select2 corretor falhou: {e}")
        return False

    async def _fixar_corretor_no_formulario(self, page, nome_corretor: str, agent_id: str = "") -> bool:
        """
        Garante que o corretor visivel no Select2 tambem vai no serialize() do formulario.
        O Select2 do Mercadoi pode mostrar o nome escolhido sem deixar o <select> pronto
        para o AJAX save_as_draft/submit_property; aqui sincronizamos os dois estados.
        """
        resultado = await page.evaluate("""
            ({nome, agentId}) => {
                const norm = (t) => String(t || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ').trim();
                const alvos = Array.from(new Set([
                    norm(nome),
                    norm(String(nome || '').replace(/^augustin/i, 'agustin')),
                    norm(String(nome || '').replace(/^agustin/i, 'augustin'))
                ])).filter(Boolean);
                const bate = (texto) => {
                    const t = norm(texto);
                    return alvos.some(alvo => t === alvo || t.includes(alvo) || alvo.includes(t));
                };

                const radios = Array.from(document.querySelectorAll('input[name="fave_agent_display_option"]'));
                const radio = radios.find(r => String(r.value) === 'agent_info')
                    || radios.find(r => /agent|corretor/i.test(String(r.value || '')))
                    || radios[0];
                if (radio) {
                    radios.forEach(r => { r.checked = false; });
                    radio.checked = true;
                    radio.dispatchEvent(new Event('input', { bubbles: true }));
                    radio.dispatchEvent(new Event('change', { bubbles: true }));
                }

                const selects = Array.from(document.querySelectorAll(
                    'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                )).filter(sel => /(fave[_-]?agents?|agent)/i.test(`${sel.name || ''} ${sel.id || ''}`));

                let valor = String(agentId || '').trim();
                let texto = nome;
                for (const sel of selects) {
                    const opt = Array.from(sel.options || []).find(o => {
                        if (valor && String(o.value) === valor) return true;
                        return bate(o.text || o.label || '');
                    });
                    if (!opt) continue;
                    valor = String(opt.value || valor).trim();
                    texto = opt.text || opt.label || texto;
                    break;
                }

                if (!valor && window.jQuery) {
                    for (const sel of selects) {
                        try {
                            const data = window.jQuery(sel).select2 ? window.jQuery(sel).select2('data') : [];
                            const item = Array.from(data || []).find(d => bate(d && d.text));
                            if (!item) continue;
                            valor = String(item.id || '').trim();
                            texto = item.text || texto;
                            break;
                        } catch (_) {}
                    }
                }

                if (!valor) return { ok: false, motivo: 'sem valor do corretor' };

                for (const sel of selects) {
                    sel.disabled = false;
                    let opt = Array.from(sel.options || []).find(o => String(o.value) === valor);
                    if (!opt) {
                        opt = new Option(texto || nome, valor, true, true);
                        sel.add(opt);
                    }
                    Array.from(sel.options || []).forEach(o => { o.selected = false; });
                    opt.selected = true;
                    if (!sel.multiple) sel.value = valor;
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.jQuery) {
                        const jq = window.jQuery(sel);
                        jq.val(sel.multiple ? [valor] : valor);
                        jq.trigger('change');
                        jq.trigger('change.select2');
                        jq.trigger({
                            type: 'select2:select',
                            params: { data: { id: valor, text: texto || nome } }
                        });
                        try { if (jq.data('select2')) jq.select2('close'); } catch (_) {}
                    }
                }

                const $form = window.jQuery && window.jQuery('#submit_property_form');
                if ($form && $form.length) {
                    $form.find('input[data-bot-agent-hidden="1"]').remove();
                    const serializadoAtual = $form.serializeArray();
                    const temAgente = serializadoAtual.some(i =>
                        /fave_agents?/i.test(i.name) && String(i.value) === String(valor)
                    );
                    if (!temAgente) {
                        for (const nomeCampo of ['fave_agents', 'fave_agents[]']) {
                            const hidden = document.createElement('input');
                            hidden.type = 'hidden';
                            hidden.name = nomeCampo;
                            hidden.value = valor;
                            hidden.setAttribute('data-bot-agent-hidden', '1');
                            $form[0].appendChild(hidden);
                        }
                    }
                }
                const serializado = $form && $form.length
                    ? $form.serializeArray().filter(i => /fave_agent_display_option|fave_agents?/i.test(i.name))
                    : [];
                return { ok: true, valor, texto, serializado };
            }
        """, {"nome": nome_corretor, "agentId": agent_id})
        if resultado and resultado.get("ok"):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
            logger.info(
                f"Corretor fixado no formulario: {resultado.get('texto') or nome_corretor} "
                f"(id={resultado.get('valor')})"
            )
            return True
        logger.warning(f"Corretor nao fixado no formulario: {resultado}")
        return False

    async def _corretor_selecionado(self, page, nome_corretor: str) -> bool:
        return await page.evaluate("""
            ({nome}) => {
                const norm = (t) => String(t || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ').trim();
                const alvos = Array.from(new Set([
                    norm(nome),
                    norm(String(nome || '').replace(/^augustin/i, 'agustin')),
                    norm(String(nome || '').replace(/^agustin/i, 'augustin'))
                ])).filter(Boolean);
                const bate = (texto) => {
                    const t = norm(texto);
                    return alvos.some(alvo => t === alvo || t.includes(alvo));
                };
                const textos = Array.from(document.querySelectorAll(
                    '.select2-selection__rendered, .select2-selection__choice'
                )).map(e => norm(e.innerText || e.getAttribute('title')));
                const selects = Array.from(document.querySelectorAll(
                    'select[name="fave_agents"], select[name="fave_agents[]"], select[id*="agent"], select[name*="agent"]'
                ));
                for (const sel of selects) {
                    for (const opt of Array.from(sel.selectedOptions || [])) {
                        textos.push(norm(opt.text || opt.label || ''));
                    }
                }
                return textos.some(t => bate(t));
            }
        """, {"nome": nome_corretor})

    @staticmethod
    def _extrair_fone_whatsapp(url: str) -> str:
        m = re.search(r"wa\.me/(\d+)", url or "")
        return m.group(1) if m else ""

    def _montar_conteudo(self, dados):
        descricao = dados.get("descricao_util", "")
        url_pub   = self._normalizar_url(dados.get("url_publicacao", ""))
        whatsapp  = self._normalizar_url(dados.get("whatsapp_url", ""))
        instagram = self._normalizar_url(dados.get("instagram_url", ""))
        fonte     = dados.get("_fonte", "")
        is_video  = fonte == "instagram"

        icones = []
        # Instagram: 3 ícones (vídeo + WhatsApp + Instagram)
        # OLX / Orulo: apenas WhatsApp
        if url_pub and is_video:
            icones.append(
                f'<a href="{url_pub}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/ver-video-mi/" width="120" height="120" '
                f'data-src="https://mercadoi.com.br/ver-video-mi/" /></a>'
            )
        if whatsapp:
            icones.append(
                f'<a href="{whatsapp}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/whatsapp-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/whatsapp-mi/" /></a>'
            )
        if instagram and is_video:
            icones.append(
                f'<a href="{instagram}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/instagram-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/instagram-mi/" /></a>'
            )

        icones_log = (
            (['Vídeo'] if url_pub and is_video else []) +
            (['WhatsApp'] if whatsapp else []) +
            (['Instagram'] if instagram and is_video else [])
        )
        if icones_log:
            logger.info(f"Ícones de contato inseridos: {' '.join(icones_log)}")

        bloco_html = ("\n\n<pre>" + "".join(icones) + "</pre>") if icones else ""

        # Linha de rastreamento: apenas o fone (sem URL para não poluir a descrição).
        # Em anúncios OLX o contato já fica anexado ao ícone do WhatsApp.
        rastreamento = ""
        fone = self._extrair_fone_whatsapp(whatsapp)
        if fone and fonte != "olx":
            rastreamento = "\n\n" + fone

        return descricao + bloco_html + rastreamento

    async def _preencher_endereco_mapa(self, page, dados: dict):
        endereco = str(dados.get("endereco", "") or dados.get("rua", "") or "").strip()
        cep = str(dados.get("cep", "") or dados.get("codigo_postal", "") or "").strip()
        latitude = str(dados.get("latitude", "") or dados.get("lat", "") or "").strip()
        longitude = str(dados.get("longitude", "") or dados.get("lng", "") or dados.get("lon", "") or "").strip()
        bairro = str(dados.get("bairro_extraido", "") or "").strip()
        cidade = str(dados.get("cidade_extraida", "") or "").strip()
        if not endereco and not cep and not latitude and not longitude and not bairro:
            return

        # Se bairro está vazio mas o endereço tem bairro embutido (ex: "Rua X 100. Jardim Y"),
        # extrai e propaga para que _selecionar_bairro possa usar
        if not bairro and endereco:
            try:
                from modules.cep_lookup import _extrair_bairro_do_endereco
                bairro_end = _extrair_bairro_do_endereco(endereco)
                if bairro_end:
                    bairro = bairro_end
                    dados["bairro_extraido"] = bairro_end
                    logger.info(f"Bairro extraido do endereco: {bairro_end}")
            except Exception:
                pass

        # Busca o CEP correto via ViaCEP (descarta CEPs de outro estado)
        try:
            from modules.cep_lookup import buscar_cep
            cep_buscado = await buscar_cep(dados)
            if cep_buscado:
                cep = cep_buscado
                dados["cep"] = cep  # propaga para XML-RPC posterior
        except Exception as e:
            logger.debug(f"Busca CEP falhou: {e}")
        try:
            resultado = await page.evaluate("""
                ({endereco, cep, latitude, longitude}) => {
                    const visivel = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                    const preencher = (seletores, valor) => {
                        if (!valor) return false;
                        for (const seletor of seletores) {
                            const campos = Array.from(document.querySelectorAll(seletor));
                            for (const el of campos) {
                                if (!el || el.disabled || el.readOnly) continue;
                                if (el.type === 'hidden' && !/(lat|lng|long|map)/i.test(el.name || el.id || '')) continue;
                                if (el.type !== 'hidden' && !visivel(el)) continue;
                                el.value = valor;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                if (window.jQuery) window.jQuery(el).val(valor).trigger('change');
                                return true;
                            }
                        }
                        return false;
                    };
                    const ok = {};
                    ok.endereco = preencher([
                        '#property_map_address',
                        '#prop_address',
                        '#fave_property_address',
                        'input[name="property_map_address"]',
                        'input[name="fave_property_address"]',
                        'input[name="property_address"]',
                        'input[name*="map"][name*="address"]'
                    ], endereco);
                    ok.cep = preencher([
                        '#property_zip',
                        '#fave_property_zip',
                        '#prop_zip',
                        '#zip',
                        'input[name="property_zip"]',
                        'input[name="fave_property_zip"]',
                        'input[name="prop_zip"]',
                        'input[name="zip"]',
                        'input[name*="cep"]',
                        'input[name*="postal"]',
                        'input[name*="zip"]',
                        'input[placeholder*="CEP"]'
                    ], cep);
                    ok.latitude = preencher([
                        '#latitude',
                        '#property_map_lat',
                        '#fave_property_location_lat',
                        'input[name="latitude"]',
                        'input[name="lat"]',
                        'input[name*="latitude"]',
                        'input[name*="[lat]"]',
                        'input[name*="map"][name*="lat"]'
                    ], latitude);
                    ok.longitude = preencher([
                        '#longitude',
                        '#property_map_lng',
                        '#property_map_long',
                        '#fave_property_location_lng',
                        'input[name="longitude"]',
                        'input[name="lng"]',
                        'input[name="long"]',
                        'input[name*="longitude"]',
                        'input[name*="[lng]"]',
                        'input[name*="map"][name*="lng"]'
                    ], longitude);
                    return ok;
                }
            """, {"endereco": endereco, "cep": cep, "latitude": latitude, "longitude": longitude})
            marcados = [k for k, ok in (resultado or {}).items() if ok]
            if marcados:
                logger.info(f"Endereco/mapa preenchido: {', '.join(marcados)}")
                # Aguarda handlers JS do formulário (autocomplete, geocoder) processarem
                await page.wait_for_timeout(800)
            else:
                logger.info("Campos de endereco/mapa nao encontrados no formulario")
        except Exception as e:
            logger.warning(f"Erro ao preencher endereco/mapa: {e}")

    @staticmethod
    def _normalizar_url(v: str) -> str:
        """Normaliza e valida uma URL. Aceita wa.me sem prefixo, retorna '' se inválida."""
        v = v.strip()
        if not v:
            return ""
        # Adiciona https:// quando ausente em wa.me e instagram.com
        if v.startswith("wa.me/") or v.startswith("instagram.com/"):
            v = "https://" + v
        if v.startswith("http://") or v.startswith("https://"):
            return v
        return ""

    async def _preencher_editor(self, page, conteudo):
        try:
            conteudo_html = conteudo.replace("\n", "<br>")

            # Guarda o conteúdo em variável JS para o AJAX usar com segurança,
            # independente do estado do TinyMCE
            await page.evaluate(
                f"() => {{ window._botDescricao = {json.dumps(conteudo_html)}; }}"
            )

            # Aguarda TinyMCE inicializar
            try:
                await page.wait_for_function(
                    "() => { const ed = window.tinymce || window.tinyMCE; return !!(ed && ed.get('prop_des')); }",
                    timeout=3000
                )
            except Exception:
                pass

            resultado = await page.evaluate(f"""
                () => {{
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    if (editor) {{
                        editor.setContent({json.dumps(conteudo_html)});
                        editor.fire('change');
                        editor.fire('input');
                        editor.save();
                        return 'tinymce';
                    }}
                    const ta = document.querySelector('#prop_des');
                    if (ta) {{
                        ta.value = {json.dumps(conteudo_html)};
                        ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                        ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return 'textarea';
                    }}
                    return null;
                }}
            """)
            if resultado:
                logger.info(f"Conteudo preenchido via {resultado}")
            else:
                logger.warning("Editor de conteudo nao encontrado")
        except Exception as e:
            logger.warning(f"Erro ao preencher editor: {e}")

    async def _preencher_campos_batch(self, page, campos: dict):
        """Preenche múltiplos inputs em uma única chamada JS."""
        try:
            preenchidos = await page.evaluate("""
                (campos) => {
                    const log = [];
                    for (const [sel, val] of Object.entries(campos)) {
                        if (!val) continue;
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        el.value = val;
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        log.push(sel + '=' + val);
                    }
                    return log;
                }
            """, campos)
            if preenchidos:
                logger.info(f"Campos preenchidos em batch: {', '.join(preenchidos)}")
        except Exception as e:
            logger.warning(f"Erro ao preencher campos em batch: {e}")

    async def _selecionar_batch(self, page, selecoes: list):
        """Seleciona múltiplos <select> em uma única chamada JS."""
        try:
            validos = [(s, v) for s, v in selecoes if v]
            if not validos:
                return
            resultado = await page.evaluate("""
                (selecoes) => {
                    const log = [];
                    const norm = t => String(t || '').toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g,'')
                        .replace(/[^a-z0-9]+/g,' ').replace(/\\s+/g,' ').trim();

                    for (const [seletor, valorAlvo] of selecoes) {
                        // Tenta pelo seletor CSS primeiro; fallback por name exato
                        let sel = null;
                        try { sel = document.querySelector(seletor); } catch(_) {}
                        if (!sel) {
                            const m = seletor.match(/\\[name="([^"]+)"\\]/);
                            if (m) sel = Array.from(document.querySelectorAll('select'))
                                              .find(s => s.name === m[1]);
                        }
                        if (!sel) continue;

                        const alvo = norm(valorAlvo);
                        let melhor = null;
                        for (const op of Array.from(sel.options)) {
                            if (!op.value) continue; // pula placeholders sem valor
                            const t = norm(op.text);
                            const b = t.replace(/^\\d+\\s*[-\\u2013]\\s*/, '');
                            if (!b || /sim.n.o|nao.sim/i.test(b)) continue; // pula "Sim/Não" combinado
                            if (t === alvo || b === alvo) { melhor = op; break; }
                            if (alvo.length > 3 && (t.includes(alvo) || (alvo.includes(b) && b.length > 3))) {
                                melhor = op; break;
                            }
                        }
                        if (!melhor) continue;

                        // Seleciona no elemento nativo
                        if (sel.multiple) {
                            Array.from(sel.options).forEach(o => { o.selected = false; });
                        }
                        melhor.selected = true;
                        if (!sel.multiple) sel.value = melhor.value;
                        sel.dispatchEvent(new Event('input',  {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));

                        // Atualiza plugins JS
                        if (window.jQuery) {
                            const jq = window.jQuery(sel);
                            if (jq.data('selectpicker')) {
                                try { jq.selectpicker('val', sel.multiple ? [melhor.value] : melhor.value); } catch(_) {}
                                try { jq.selectpicker('refresh'); } catch(_) {}
                            } else if (jq.data('select2')) {
                                jq.val(sel.multiple ? [melhor.value] : melhor.value).trigger('change');
                            } else {
                                jq.val(sel.multiple ? [melhor.value] : melhor.value).trigger('change');
                            }
                        }
                        log.push(seletor.replace(/select\\[name="([^"]+)"\\]/, '$1') + '=' + melhor.text.trim());
                    }
                    return log;
                }
            """, validos)
            if resultado:
                logger.info(f"Detalhes (batch): {', '.join(resultado)}")
        except Exception as e:
            logger.warning(f"Erro ao selecionar em batch: {e}")

    async def _marcar_caracteristicas(self, page, caracteristicas):
        """Marca checkboxes de caracteristicas pelo texto visivel do formulario."""
        if isinstance(caracteristicas, str):
            caracteristicas = [p.strip() for p in re.split(r"[,;\n]+", caracteristicas) if p.strip()]
        alvos_raw = list(dict.fromkeys([str(c).strip() for c in (caracteristicas or []) if str(c).strip()]))

        # Filtra itens da blocklist antes de enviar ao JS
        # IMPORTANTE: usa apenas 'b in cn' (termo bloqueado dentro do nome da característica)
        # NÃO usa 'cn in b' — isso bloquearia 'Portaria' por ser substring de 'Portaria 24h'
        def _bloqueado(c: str) -> bool:
            cn = normalizar(c)
            return any(b in cn for b in _NUNCA_MARCAR)

        alvos = [c for c in alvos_raw if not _bloqueado(c)]
        if alvos_raw != alvos:
            bloq = [c for c in alvos_raw if _bloqueado(c)]
            logger.info(f"Caracteristicas bloqueadas (nunca marcar): {', '.join(bloq)}")
        if not alvos:
            return
        try:
            resultado = await page.evaluate("""
                (alvos) => {
                    const norm = (t) => String(t || '')
                        .toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/[^a-z0-9]+/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();

                    // Labels que nunca devem ser marcados no formulário (segurança extra)
                    const BLOQUEADOS = new Set([
                        'academia', 'banheiro social', 'biblioteca',
                        'circuito de seguranca', 'camera de seguranca',
                        'espaco gourmet', 'lounge',
                        'portaria 24h', 'portaria eletronica',
                        'salao de festas / sum', 'salao de festas', 'salao de jogos',
                        'spa',
                        'terraco', 'terraco rooftop', 'rooftop',
                        'sistema de alarme', 'piscina infantil', 'piscina privativa',
                    ]);

                    // Detecta se o checkbox está na seção "Áreas Privativas"
                    function secao(el) {
                        let cur = el;
                        for (let i = 0; i < 12 && cur && cur !== document.body; i++) {
                            // Verifica irmão anterior (heading de seção)
                            let prev = cur.previousElementSibling;
                            while (prev) {
                                const t = norm(prev.textContent || '');
                                if (t.includes('privat')) return 'privativa';
                                if (t.includes('comum')) return 'comum';
                                prev = prev.previousElementSibling;
                            }
                            cur = cur.parentElement;
                        }
                        return '';
                    }

                    const textoCheckbox = (input) => {
                        const textos = [];
                        if (input.id) {
                            const lab = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                            if (lab) textos.push(lab.innerText || lab.textContent || '');
                        }
                        const labelPai = input.closest('label');
                        if (labelPai) textos.push(labelPai.innerText || labelPai.textContent || '');

                        let el = input.nextSibling;
                        let hops = 0;
                        while (el && hops < 4) {
                            if (el.nodeType === Node.TEXT_NODE) textos.push(el.textContent || '');
                            if (el.nodeType === Node.ELEMENT_NODE) textos.push(el.innerText || el.textContent || '');
                            el = el.nextSibling;
                            hops++;
                        }

                        const parent = input.parentElement;
                        if (parent) textos.push(parent.innerText || parent.textContent || '');

                        return textos
                            .map(t => String(t || '').replace(/\\s+/g, ' ').trim())
                            .filter(Boolean)
                            .sort((a, b) => a.length - b.length)[0] || '';
                    };

                    const alvosNorm = alvos.map(a => {
                        const original = String(a || '');
                        let n = norm(original);
                        const privativa = n.startsWith('area privativa ') || n.startsWith('privativa ');
                        n = n
                            .replace(/^area privativa /, '')
                            .replace(/^privativa /, '')
                            .replace(/^area comum /, '')
                            .replace(/^comum /, '')
                            .trim();
                        return {original, norm: n, privativa};
                    }).filter(a => a.norm);
                    const marcadas = [];
                    const faltantes = new Map(alvosNorm.map(a => [a.norm, a.original]));
                    const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'))
                        .map(box => {
                            const texto = textoCheckbox(box);
                            return {box, texto, textoNorm: norm(texto), sec: secao(box)};
                        })
                        .filter(c => c.textoNorm && !BLOQUEADOS.has(c.textoNorm));

                    const match = (c, alvo) => {
                        if (alvo.privativa) {
                            return c.sec === 'privativa' && c.textoNorm === alvo.norm;
                        }
                        if (c.sec === 'privativa') return false;
                        return c.textoNorm === alvo.norm ||
                               c.textoNorm.includes(alvo.norm) ||
                               alvo.norm.includes(c.textoNorm);
                    };
                    const score = (c) => c.sec === 'comum' ? 0 : 1;

                    for (const alvo of alvosNorm) {
                        const candidatos = boxes
                            .filter(c => match(c, alvo))
                            .sort((a, b) => score(a) - score(b) || a.texto.length - b.texto.length);
                        const escolhido = candidatos[0];
                        if (!escolhido) continue;

                        const box = escolhido.box;
                        if (!box.checked) box.click();
                        box.dispatchEvent(new Event('input', {bubbles: true}));
                        box.dispatchEvent(new Event('change', {bubbles: true}));
                        marcadas.push(`${escolhido.texto}${escolhido.sec === 'privativa' ? ' [priv]' : ''}`);
                        faltantes.delete(alvo.norm);
                    }

                    return {
                        marcadas: Array.from(new Set(marcadas)),
                        faltantes: Array.from(faltantes.values()),
                    };
                }
            """, alvos)
            marcadas = resultado.get("marcadas", []) if isinstance(resultado, dict) else []
            faltantes = resultado.get("faltantes", []) if isinstance(resultado, dict) else []
            if marcadas:
                logger.info(f"Caracteristicas marcadas: {', '.join(marcadas[:20])}")
            if faltantes:
                logger.info(f"Caracteristicas nao encontradas no formulario: {', '.join(faltantes[:20])}")
        except Exception as e:
            logger.warning(f"Erro ao marcar caracteristicas: {e}")

    def _detalhes_adicionais(self, dados: dict) -> dict:
        def _s(key: str) -> str:
            v = dados.get(key, "")
            return v.strip() if isinstance(v, str) else str(v or "").strip()

        def sim_nao(valor: str) -> str:
            n = normalizar(valor)
            if not n:
                return ""
            if n in {"nao", "n", "no", "false", "não"} or "nao aceita" in n or "não aceita" in n:
                return "Não"
            if n in {"sim", "s", "yes", "true"} or "aceita" in n or "possui" in n:
                return "Sim"
            return valor.strip()

        def estagio(valor: str) -> str:
            n = normalizar(valor)
            # Valores EXATOS do formulário Mercadoi (incluindo typo "Contrução")
            mapa = [
                (("breve",),                              "1- Em breve lançamento"),
                (("lancamento", "lançamento"),            "2- Lançamento"),
                (("construcao", "construção", "contrucao", "obra", "obras"), "3- Em Contrução"),
                (("novo",),                               "4- Novo"),
                (("semi novo", "seminovo"),               "5- Semi Novo"),
                (("usado",),                              "6- Usado"),
                (("reformado",),                          "7- Reformado"),
            ]
            for chaves, opcao in mapa:
                if any(c in n for c in chaves):
                    return opcao
            return valor.strip()

        def mobiliado(valor: str) -> str:
            n = normalizar(valor)
            if not n:
                return ""
            if "decor" in n:
                return "Mobiliado e decorado"
            if "semi" in n:
                return "Semi-mobiliado"
            if "sem" in n and ("mobil" in n or "mob" in n):
                return "Sem mobília"
            if "mobil" in n:
                return "Mobiliado"
            return valor.strip()

        def posicao_solar(valor: str) -> str:
            n = normalizar(valor)
            opcoes = [
                ("sol da manha e tarde", "Sol da manhã e tarde"),
                ("sol da manha", "Sol da manhã"),
                ("sol da tarde", "Sol da tarde"),
                ("nordeste", "Nordeste"),
                ("sudeste", "Sudeste"),
                ("sudoeste", "Sudoeste"),
                ("noroeste", "Noroeste"),
                ("norte", "Norte"),
                ("sul", "Sul"),
                ("leste", "Leste"),
                ("oeste", "Oeste"),
                ("nascente", "Leste"),
                ("poente", "Oeste"),
            ]
            for chave, opcao in opcoes:
                if chave in n:
                    return opcao
            return valor.strip()

        def perto_mar(valor: str) -> str:
            n = normalizar(valor)
            if not n:
                return ""
            if "vista" in n:
                return "Vista para o mar"
            if "frente" in n or "beira mar" in n:
                return "Frente para o mar"
            if "quadra" in n:
                return "Quadra do mar"
            if "mar" in n or "praia" in n:
                return "Próximo ao mar"
            return valor.strip()

        def posicao_predio(valor: str) -> str:
            n = normalizar(valor)
            for chave, opcao in [("frente", "Frente"), ("fundo", "Fundo"), ("lateral", "Lateral"), ("meio", "Meio")]:
                if chave in n:
                    return opcao
            return valor.strip()

        def andar(valor: str) -> str:
            n = normalizar(valor)
            if not n:
                return ""
            if "terreo" in n or "terrco" in n or "ground" in n or n == "0":
                return "Terreo"
            # extrai o primeiro número inteiro encontrado (ex: "3o andar" → "3")
            m = re.search(r"\b(\d{1,3})\b", valor)
            if m:
                return m.group(1)
            return ""

        def _limpar_valor(v: str) -> str:
            # remove "R$", "r$", "R$ ", pontos de milhar; mantém vírgula decimal
            v = re.sub(r"[Rr]\$\s*", "", v).strip()
            # remove pontos de milhar (ex: 1.200 → 1200) mas mantém decimal com vírgula
            v = re.sub(r"\.(?=\d{3})", "", v)
            return v.strip()

        cond = _limpar_valor(_s("condominio"))
        iptu = _limpar_valor(_s("iptu"))
        taxas = _limpar_valor(_s("taxas"))
        # monta "cond/iptu/taxas" preservando slots vazios internos (ex: 100//500)
        # mas omite trailing separadores se os últimos slots estiverem vazios
        partes = [cond, iptu, taxas]
        while partes and not partes[-1]:
            partes.pop()
        cond_taxas_str = "/".join(partes) if partes else ""

        return {
            "selects": {
                "Estágio do Imovel": estagio(_s("estagio_imovel")),
                "Andar": andar(_s("andar")),
                "Tem elevador?": sim_nao(_s("elevador")),
                "Posição Solar": posicao_solar(_s("posicao_solar")),
                "Perto do mar?": perto_mar(_s("perto_do_mar")),
                "Escriturado?": sim_nao(_s("escriturado")),
                "Aceita Permuta?": sim_nao(_s("aceita_permuta")),
                "Posição no Prédio": posicao_predio(_s("posicao_predio")),
                "Mobiliado?": mobiliado(_s("mobiliado")),
                "Aceita Airbnb/Temporada": sim_nao(_s("aceita_airbnb")),
                "Aceita Financiamento?": sim_nao(_s("aceita_financiamento")),
            },
            "inputs": {
                "Área total m²": _s("area_terreno"),
                "Condomínio - IPTU - Taxas": cond_taxas_str,
                "Ano de construção": _s("ano_construcao"),
                "Proximidades": _s("proximidades"),
            },
        }

    async def _preencher_detalhes_adicionais(self, page, dados: dict):
        # Apenas campos de texto livre — os selects são tratados por _selecionar_batch
        detalhes = self._detalhes_adicionais(dados)
        selects = {}  # não preenche selects aqui
        inputs  = {k: v for k, v in detalhes["inputs"].items()  if v}
        if not inputs:
            return
        try:
            resultado = await page.evaluate("""
                ({selects, inputs}) => {
                    const norm = (t) => String(t || '')
                        .toLowerCase()
                        .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/[^a-z0-9]+/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();

                    const controles = 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea, select';

                    const opTexto = (op) => String(op.text || op.label || '').replace(/\\s+/g, ' ').trim();

                    // Encontra campo por label — tenta visível primeiro, depois qualquer
                    const acharCampo = (rotulo) => {
                        const alvo = norm(rotulo);
                        const labels = Array.from(document.querySelectorAll(
                            'label, .control-label, .form-label, th, legend'
                        ));
                        for (const label of labels) {
                            const texto = norm(label.innerText || label.textContent || '');
                            if (!texto) continue;
                            if (!(texto === alvo || texto.includes(alvo) || alvo.includes(texto))) continue;

                            // Via atributo for
                            const forId = label.getAttribute('for');
                            if (forId) {
                                const el = document.getElementById(forId);
                                if (el && el.matches(controles)) return el;
                            }

                            // Campo próximo — sem exigir visibilidade (aceita seções collapsed)
                            let raiz = label.parentElement;
                            for (let i = 0; i < 6 && raiz; i++, raiz = raiz.parentElement) {
                                const depois = Array.from(raiz.querySelectorAll(controles)).filter(c =>
                                    !!(label.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)
                                );
                                if (depois.length) return depois[0];
                            }
                        }
                        // Fallback: name / placeholder / id
                        return Array.from(document.querySelectorAll(controles)).find(c => {
                            const ph = norm(c.getAttribute('placeholder') || '');
                            const nm = norm(c.getAttribute('name') || c.id || '');
                            return (ph && (ph.includes(alvo) || alvo.includes(ph)))
                                || (nm && nm.includes(alvo));
                        }) || null;
                    };

                    const aplicarSelect = (select, valor) => {
                        const alvo = norm(valor);
                        let melhor = null;
                        for (const op of Array.from(select.options || [])) {
                            if (!op.value || op.value === '0') continue; // pula placeholder sem valor
                            const t    = norm(op.text || op.label || '');
                            const base = t.replace(/^\\d+\\s*[-\\u2013]\\s*/, '');
                            // pula opções placeholder combinadas (ex: "Sim/Não", "Selecione")
                            if (!base || base === 'selecione' || /sim.n.o|nao.sim/i.test(base)) continue;
                            if (t === alvo || base === alvo) { melhor = op; break; }
                            if (alvo.length > 2 && (t.includes(alvo) || alvo.includes(base))) {
                                melhor = op; break;
                            }
                            // Match numérico para andar (ex: "3" bate em "3 andar")
                            const alvoN = (alvo.match(/\\d+/) || [''])[0];
                            const baseN = (base.match(/\\d+/) || [''])[0];
                            if (alvoN && baseN && alvoN === baseN) { melhor = op; break; }
                        }
                        if (!melhor) return null;
                        try { select.scrollIntoView({behavior: 'instant', block: 'center'}); } catch(_) {}

                        // Marca a opcao no elemento nativo
                        if (select.multiple) {
                            Array.from(select.options).forEach(o => { o.selected = false; });
                        }
                        melhor.selected = true;
                        if (!select.multiple) select.value = melhor.value;
                        select.dispatchEvent(new Event('input',  {bubbles: true}));
                        select.dispatchEvent(new Event('change', {bubbles: true}));

                        if (window.jQuery) {
                            const jq = window.jQuery(select);
                            // Bootstrap-select (selectpicker)
                            if (jq.data('selectpicker')) {
                                try { jq.selectpicker('val', [melhor.value]); } catch(_) {}
                                try { jq.selectpicker('refresh'); } catch(_) {}
                            }
                            // Select2
                            if (jq.data('select2')) {
                                jq.val(select.multiple ? [melhor.value] : melhor.value).trigger('change');
                                try { jq.trigger('change.select2'); } catch(_) {}
                            }
                            // Fallback genérico
                            if (!jq.data('selectpicker') && !jq.data('select2')) {
                                jq.val(select.multiple ? [melhor.value] : melhor.value).trigger('change');
                            }
                        }
                        return opTexto(melhor);
                    };

                    const preenchidos = [];

                    for (const [rotulo, valor] of Object.entries(inputs || {})) {
                        if (!valor) continue;
                        const campo = acharCampo(rotulo);
                        if (!campo || campo.tagName === 'SELECT') continue;
                        try { campo.scrollIntoView({behavior: 'instant', block: 'center'}); } catch(_) {}
                        campo.value = valor;
                        campo.dispatchEvent(new Event('input',  {bubbles: true}));
                        campo.dispatchEvent(new Event('change', {bubbles: true}));
                        preenchidos.push(rotulo + '=' + valor);
                    }

                    for (const [rotulo, valor] of Object.entries(selects || {})) {
                        if (!valor) continue;
                        const campo = acharCampo(rotulo);
                        if (!campo || campo.tagName !== 'SELECT') {
                            preenchidos.push(rotulo + '=NAO_ENCONTRADO');
                            continue;
                        }
                        const escolhido = aplicarSelect(campo, valor);
                        preenchidos.push(rotulo + '=' + (escolhido || 'OPCAO_NAO_ENCONTRADA(' + valor + ')'));
                    }

                    return preenchidos;
                }
            """, {"selects": selects, "inputs": inputs})
            if resultado:
                encontrados = [r for r in resultado if 'NAO_ENCONTRADO' not in r and 'OPCAO_NAO_ENCONTRADA' not in r]
                falhas = [r for r in resultado if 'NAO_ENCONTRADO' in r or 'OPCAO_NAO_ENCONTRADA' in r]
                if encontrados:
                    logger.info(f"Detalhes preenchidos: {', '.join(encontrados)}")
                if falhas:
                    logger.warning(f"Detalhes nao encontrados no formulario: {', '.join(falhas)}")
        except Exception as e:
            logger.warning(f"Erro ao preencher detalhes adicionais: {e}")

    def _cidade_por_bairro(self, bairro: str) -> str:
        b = normalizar(bairro)
        # Busca exata primeiro, depois substring
        if b in _BAIRRO_CIDADE_PB:
            return _BAIRRO_CIDADE_PB[b]
        for key, cidade in _BAIRRO_CIDADE_PB.items():
            if key in b or b in key:
                return cidade
        return ""

    async def _selecionar_cidade(self, page, cidade, bairro=""):
        if cidade:
            ok = await self._selecionar_por_texto(page, '#city', cidade)
            if ok:
                await self._aguardar_opcoes_bairro(page)
                return cidade
        # Tenta inferir cidade pelo bairro
        if bairro:
            cidade_inferida = self._cidade_por_bairro(bairro)
            if cidade_inferida:
                ok = await self._selecionar_por_texto(page, '#city', cidade_inferida)
                if ok:
                    logger.info(f"Cidade inferida pelo bairro '{bairro}': {cidade_inferida}")
                    await self._aguardar_opcoes_bairro(page)
                    return cidade_inferida
        logger.info(f"Cidade '{cidade}' nao encontrada, usando Joao Pessoa")
        await self._selecionar_por_texto(page, '#city', "João Pessoa")
        await self._aguardar_opcoes_bairro(page)
        return "Joao Pessoa"

    async def _aguardar_opcoes_bairro(self, page, timeout_s: int = 3):
        """Aguarda o select #neighborhood ser populado via AJAX após seleção da cidade."""
        try:
            await page.wait_for_function(
                "() => { const s = document.querySelector('#neighborhood'); return s && s.options.length > 1; }",
                timeout=timeout_s * 1000
            )
        except Exception:
            logger.warning("Timeout aguardando opções de bairro")

    # Palavras genéricas de início de bairro que não servem como chave de busca parcial
    _PREFIXOS_BAIRRO_GENERICOS = {
        "bairro", "jardim", "vila", "parque", "setor", "conjunto", "residencial",
        "loteamento", "distrito", "zona", "area", "rua", "avenida", "praia",
    }

    async def _selecionar_bairro(self, page, bairro):
        if not bairro:
            return ""

        # Tenta o nome completo primeiro
        if await self._selecionar_por_texto(page, '#neighborhood', bairro):
            return bairro

        palavras = bairro.split()
        # Fallback por palavra mais específica: pula prefixos genéricos do início
        for i, palavra in enumerate(palavras):
            if normalizar(palavra) in self._PREFIXOS_BAIRRO_GENERICOS:
                continue
            if len(palavra) < 4:
                continue
            # Tenta a partir dessa palavra em diante (ex: "Bairro das Indústrias" → "Indústrias")
            candidato = " ".join(palavras[i:])
            if candidato != bairro and await self._selecionar_por_texto(page, '#neighborhood', candidato):
                logger.info(f"Bairro parcial aceito: '{candidato}' (original: '{bairro}')")
                return candidato

        logger.info(f"Bairro '{bairro}' nao encontrado no select — campo deixado em branco")
        return ""

    def _validar_arquivos(self, caminhos: list) -> list:
        """Valida arquivos e converte formatos não suportados antes do upload.

        JPEG e PNG são aceitos diretamente pelo Mercadoi.
        WEBP, BMP, TIFF, GIF e outros são convertidos para JPEG.
        """
        from PIL import Image as _PIL
        FORMATOS_OK = {"JPEG", "PNG"}
        EXTENSOES_OK = {".jpg", ".jpeg", ".png"}
        validos = []
        for c in caminhos:
            if not os.path.exists(c) or os.path.getsize(c) == 0:
                logger.warning(f"Arquivo ausente ou vazio, ignorado: {c}")
                continue
            try:
                with _PIL.open(c) as img:
                    fmt = img.format
                    mode = img.mode

                ext = os.path.splitext(c)[1].lower()
                formato_suportado = fmt in FORMATOS_OK and ext in EXTENSOES_OK

                if formato_suportado:
                    validos.append(c)
                else:
                    # Converte WEBP, BMP, TIFF, GIF, etc. para JPEG
                    novo = os.path.splitext(c)[0] + "_conv.jpg"
                    with _PIL.open(c) as img:
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        img.save(novo, "JPEG", quality=92, optimize=True)
                    logger.info(f"Convertido para JPEG: {os.path.basename(c)} (era {fmt}/{mode}) → {os.path.basename(novo)}")
                    validos.append(novo)
            except Exception as e:
                logger.warning(f"Imagem inválida/corrompida, ignorada: {c} — {e}")
        return validos

    async def _anexar_midia(self, page, caminhos):
        """Anexa uma ou mais imagens à galeria via plupload."""
        if isinstance(caminhos, str):
            caminhos = [caminhos]
        if not caminhos:
            return False
        caminhos = self._validar_arquivos(caminhos)
        if not caminhos:
            logger.error("Nenhum arquivo válido para upload após validação")
            return False
        try:
            esperados = len(caminhos)
            inicial = await self._contar_midias_anexadas(page)
            alvo_final = inicial + esperados
            logger.info(f"Galeria inicial: {inicial}; anexando {esperados} arquivo(s)")

            # Alguns ambientes/plupload aceitam apenas parte dos arquivos quando
            # mandamos uma selecao grande. Enviar em lotes evita perder os ultimos.
            tamanho_lote = 3
            for inicio in range(0, esperados, tamanho_lote):
                lote = caminhos[inicio:inicio + tamanho_lote]
                alvo_lote = inicial + inicio + len(lote)
                if not await self._enviar_lote_upload(page, lote):
                    return False

                confirmados = await self._aguardar_uploads(page, alvo_lote, esperados=alvo_final)
                if confirmados < alvo_lote:
                    logger.warning(
                        f"Lote de upload incompleto: {confirmados - inicial}/{inicio + len(lote)} "
                        f"arquivo(s) confirmados"
                    )
                    break

            confirmados_final = await self._aguardar_uploads(page, alvo_final, esperados=alvo_final)
            recebidos = max(0, confirmados_final - inicial)
            if confirmados_final >= alvo_final:
                logger.info(f"Todos os arquivos confirmados na galeria ({recebidos}/{esperados})")
                return True

            logger.error(f"Upload incompleto: {recebidos}/{esperados} arquivo(s) confirmados")
            return False

            enviado = False
            upload_btn = None
            for sel in ['#select_gallery_images', '#plupload-browse-button', 'a.plupload_add', '.plupload_add',
                        'a[id*="browse"]', 'button[id*="browse"]', 'div[id*="browse"]',
                        'a[id*="select"]', 'a[id*="upload"]', 'a[id*="gallery"]']:
                upload_btn = await page.query_selector(sel)
                if upload_btn:
                    logger.info(f"Usando botao de upload: {sel}")
                    break

            if upload_btn:
                try:
                    async with page.expect_file_chooser(timeout=5000) as fc_info:
                        await upload_btn.click()
                    fc = await fc_info.value
                    await fc.set_files(caminhos)
                    enviado = True
                    logger.info(f"{esperados} arquivo(s) enviado(s) via file_chooser")
                except Exception as e:
                    logger.warning(f"File chooser nao abriu, tentando input file direto: {e}")

            if not enviado:
                input_file = await self._localizar_input_upload(page)
                if input_file:
                    await input_file.set_input_files(caminhos)
                    enviado = True
                    logger.info(f"{esperados} arquivo(s) enviado(s) via input file")
                else:
                    logger.warning("Campo de upload nao encontrado")
                    return False

            # Aguarda upload iniciar no servidor
            await page.wait_for_timeout(500)

            # Detecta erros do plupload exibidos na página
            erro_plupload = await page.evaluate("""
                () => {
                    const errs = document.querySelectorAll(
                        '.plupload_error, .moxie-shim-error, [class*="error"][class*="upload"], .plupload .error'
                    );
                    return Array.from(errs).map(e => e.innerText).filter(t => t).join(' | ');
                }
            """)
            if erro_plupload:
                logger.warning(f"Erro de upload detectado na página: {erro_plupload}")

            ids = []
            limite = max(esperados * 6, 16)
            for i in range(limite):
                await page.wait_for_timeout(300 if i < 6 else 500)

                # Verifica campos ocultos (Mercadoi usa "propperty" com pp duplo, mas também testa variante)
                ids = await page.evaluate("""
                    () => {
                        const sels = [
                            'input[name="propperty_image_ids[]"]',
                            'input[name="property_image_ids[]"]',
                            'input[name*="image_ids"]',
                        ];
                        for (const s of sels) {
                            const found = Array.from(document.querySelectorAll(s))
                                .map(el => el.value).filter(v => v && v !== '0');
                            if (found.length) return found;
                        }
                        return [];
                    }
                """)

                # Verifica thumbnails visíveis na galeria como confirmação alternativa
                thumbs = await page.evaluate("""
                    () => {
                        const sels = [
                            '.fave_property_images .preview-item',
                            '.fave_property_images img',
                            '.property-gallery-upload img',
                            'ul.fave_images_list li',
                            '[class*="gallery"] .thumbnail',
                            '[class*="upload"] img[src*="uploads"]',
                        ];
                        for (const s of sels) {
                            const n = document.querySelectorAll(s).length;
                            if (n > 0) return n;
                        }
                        return 0;
                    }
                """)

                confirmados = max(len(ids), thumbs if isinstance(thumbs, int) else 0)
                logger.info(f"Uploads concluidos: {confirmados}/{esperados} (ids={len(ids)}, thumbs={thumbs})")

                if len(ids) >= esperados:
                    logger.info("Todos os arquivos confirmados via IDs")
                    return True
                if isinstance(thumbs, int) and thumbs >= esperados:
                    logger.info(f"Todos os arquivos confirmados via thumbnails ({thumbs})")
                    return True

            # Aceita parcial se ao menos 1 foi confirmado
            confirmados_final = max(len(ids), thumbs if isinstance(thumbs, int) else 0)
            if confirmados_final > 0:
                logger.warning(f"Upload parcial aceito: {confirmados_final}/{esperados} arquivo(s)")
                return True

            logger.error(f"Upload falhou: nenhuma imagem confirmada no servidor ({esperados} esperado(s))")
            return False
        except Exception as e:
            logger.error(f"Erro ao anexar midia: {e}")
            return False

    async def _enviar_lote_upload(self, page, caminhos: list) -> bool:
        enviado = False
        upload_btn = None
        for sel in ['#select_gallery_images', '#plupload-browse-button', 'a.plupload_add', '.plupload_add',
                    'a[id*="browse"]', 'button[id*="browse"]', 'div[id*="browse"]',
                    'a[id*="select"]', 'a[id*="upload"]', 'a[id*="gallery"]']:
            upload_btn = await page.query_selector(sel)
            if upload_btn:
                logger.info(f"Usando botao de upload: {sel}")
                break

        if upload_btn:
            try:
                async with page.expect_file_chooser(timeout=5000) as fc_info:
                    await upload_btn.click()
                fc = await fc_info.value
                await fc.set_files(caminhos)
                enviado = True
                logger.info(f"{len(caminhos)} arquivo(s) enviado(s) via file_chooser")
            except Exception as e:
                logger.warning(f"File chooser nao abriu, tentando input file direto: {e}")

        if not enviado:
            input_file = await self._localizar_input_upload(page)
            if input_file:
                await input_file.set_input_files(caminhos)
                enviado = True
                logger.info(f"{len(caminhos)} arquivo(s) enviado(s) via input file")
            else:
                logger.warning("Campo de upload nao encontrado")
                return False

        await page.wait_for_timeout(500)
        erro_plupload = await page.evaluate("""
            () => {
                const errs = document.querySelectorAll(
                    '.plupload_error, .moxie-shim-error, [class*="error"][class*="upload"], .plupload .error'
                );
                return Array.from(errs).map(e => e.innerText).filter(t => t).join(' | ');
            }
        """)
        if erro_plupload:
            logger.warning(f"Erro de upload detectado na pagina: {erro_plupload}")
        return True

    async def _contar_midias_anexadas(self, page) -> int:
        dados = await page.evaluate("""
            () => {
                const idSels = [
                    'input[name="propperty_image_ids[]"]',
                    'input[name="property_image_ids[]"]',
                    'input[name*="image_ids"]',
                ];
                const ids = new Set();
                for (const s of idSels) {
                    document.querySelectorAll(s).forEach(el => {
                        const v = (el.value || '').trim();
                        if (v && v !== '0') ids.add(v);
                    });
                }

                const thumbSels = [
                    '.fave_property_images .preview-item',
                    '.fave_property_images img',
                    '.property-gallery-upload img',
                    'ul.fave_images_list li',
                    '[class*="gallery"] .thumbnail',
                    '[class*="upload"] img[src*="uploads"]',
                ];
                let thumbs = 0;
                for (const s of thumbSels) {
                    thumbs = Math.max(thumbs, document.querySelectorAll(s).length);
                }
                return {ids: ids.size, thumbs};
            }
        """)
        ids = int(dados.get("ids") or 0)
        thumbs = int(dados.get("thumbs") or 0)
        return max(ids, thumbs)

    async def _aguardar_uploads(self, page, alvo: int, esperados: int) -> int:
        confirmados = 0
        limite = max(esperados * 8, 24)
        for i in range(limite):
            await page.wait_for_timeout(300 if i < 6 else 500)
            confirmados = await self._contar_midias_anexadas(page)
            logger.info(f"Uploads concluidos: {confirmados}/{alvo}")
            if confirmados >= alvo:
                return confirmados
        return confirmados

    async def _localizar_input_upload(self, page):
        try:
            handles = await page.query_selector_all('input[type="file"]')
            for handle in handles:
                try:
                    if await handle.is_visible():
                        return handle
                except Exception:
                    continue
            return handles[0] if handles else None
        except Exception:
            return None

    @staticmethod
    def _extra_campos_str(extra_campos: dict | None) -> str:
        """Serializa campos extras para concatenar no AJAX POST."""
        from urllib.parse import quote as _q
        if not extra_campos:
            return ""
        return "".join(
            f"&{_q(k, safe='[]')}={_q(str(v), safe='')}"
            for k, v in extra_campos.items() if v
        )

    async def _salvar_rascunho(self, page, agent_id: str = "", extra_campos: dict | None = None):
        try:
            try:
                await page.wait_for_selector('#save_as_draft', timeout=5000)
            except Exception:
                logger.warning("Timeout aguardando #save_as_draft, tentando mesmo assim")

            extra_str = self._extra_campos_str(extra_campos)

            resultado = await page.evaluate("""
                ({agentId, extraCampos}) => new Promise((resolve) => {
                    const $form = jQuery('#submit_property_form');
                    if (!$form.length) { resolve({ok: false, erro: 'form nao encontrado'}); return; }
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    const description = editor
                        ? editor.getContent()
                        : (window._botDescricao || (document.querySelector('#prop_des') || {}).value || '');
                    const ajaxUrl = window.ajax_url || window.ajaxurl || '/wp-admin/admin-ajax.php';
                    let extraAgent = '';
                    if (agentId) {
                        extraAgent = '&fave_agents=' + encodeURIComponent(agentId)
                                   + '&fave_agent_display_option=agent_info';
                    }
                    const formData = $form.serialize()
                        + '&action=save_as_draft&description=' + encodeURIComponent(description)
                        + extraAgent + extraCampos;
                    jQuery.ajax({
                        type: 'post',
                        url: ajaxUrl,
                        dataType: 'json',
                        data: formData,
                        success: function(response) { resolve({ok: true, response: JSON.stringify(response)}); },
                        error: function(xhr, status, error) {
                            resolve({ok: false, erro: error, status: status, resp: xhr.responseText.substring(0, 300)});
                        }
                    });
                })
            """, {"agentId": agent_id, "extraCampos": extra_str})

            logger.info(f"Resultado AJAX save_as_draft: {resultado}")

            if not resultado or not resultado.get('ok'):
                logger.error(f"AJAX falhou: {resultado}")
                return {"ok": False}

            try:
                resp = json.loads(resultado.get('response', '{}'))
                if resp.get('success') or resp.get('suc'):
                    logger.info("Rascunho salvo com sucesso via AJAX")
                    property_id = resp.get("property_id") or resp.get("prop_id") or resp.get("id")
                    url = self._montar_url_mercadoi(property_id)
                    return {"ok": True, "property_id": property_id, "url": url}
                else:
                    logger.error(f"AJAX retornou falha: {resp}")
                    return {"ok": False}
            except Exception:
                logger.info("AJAX completou (resposta aceita)")
                return {"ok": True}

        except Exception as e:
            logger.error(f"Erro ao salvar rascunho: {e}")
            return {"ok": False}

    def _atualizar_meta_detalhes(self, post_id, dados: dict):
        """Atualiza meta de detalhes via XML-RPC apos salvar o post no Mercadoi."""
        if not post_id or not self._wp_user or not self._wp_pass:
            return
        try:
            import xmlrpc.client
            det = self._detalhes_adicionais(dados)
            dv = det["selects"]
            iv = det["inputs"]
            estagio_meta = dv.get("Estágio do Imovel", "")
            if not estagio_meta:
                desc = str(dados.get("descricao_util") or "")
                m_estagio = re.search(r"Est[aá]gio\s*:\s*([^<\n\r-]+)", desc, re.IGNORECASE)
                if m_estagio:
                    estagio_meta = self._detalhes_adicionais({
                        "estagio_imovel": m_estagio.group(1).strip()
                    })["selects"].get("Estágio do Imovel", "")

            mapeamento = {
                "fave_tem-elevador":              dv.get("Tem elevador?", ""),
                "fave_mobiliado":                 dv.get("Mobiliado?", ""),
                "fave_escriturado":               dv.get("Escriturado?", ""),
                "fave_aceita-airbnb-temporada":   dv.get("Aceita Airbnb/Temporada", ""),
                "fave_aceita-permuta":            dv.get("Aceita Permuta?", ""),
                "fave_aceita-financiamento":      dv.get("Aceita Financiamento?", ""),
                "fave_posic3a7c3a3o-do-imovel":  dv.get("Posição Solar", ""),
                "fave_posic3a7c3a3o":             dv.get("Posição no Prédio", ""),
                "fave_property_bedrooms":          str(dados.get("quartos") or "").strip(),
                "fave_property_bathrooms":         str(dados.get("banheiros") or "").strip(),
                "fave_property_rooms":             str(dados.get("suites") or "").strip(),
                "fave_estagio-da-obra-imc3b3vel": estagio_meta,
                "fave_no-tc3a9rreo":              dv.get("Andar", ""),
                "fave_perto-do-mar":              dv.get("Perto do mar?", ""),
                "fave_property_land":             iv.get("Área total m²", ""),
                "fave_condomc3adnio-iptu-taxas":  iv.get("Condomínio - IPTU - Taxas", ""),
                "fave_property_year":             iv.get("Ano de construção", ""),
                "fave_vizinhanc3a7a":             iv.get("Proximidades", ""),
                # Endereço e CEP via XML-RPC como garantia extra
                "fave_property_address":          str(dados.get("endereco") or dados.get("rua") or "").strip(),
                "fave_property_map_address":      str(dados.get("endereco") or dados.get("rua") or "").strip(),
                "fave_property_zip":              str(dados.get("cep") or "").strip(),
            }

            # Para posts Orulo, garante o corretor via meta também
            agent_id = str(dados.get("_mercadoi_agent_id", "")).strip()
            if dados.get("_fonte") == "orulo" and agent_id:
                mapeamento["fave_agents"] = agent_id
                mapeamento["fave_agent_display_option"] = "agent_info"

            xmlrpc_url = self.base_url.rstrip("/") + "/xmlrpc.php"
            proxy = xmlrpc.client.ServerProxy(xmlrpc_url)

            existentes = {}
            post_atual = {}
            try:
                post_atual = proxy.wp.getPost("1", self._wp_user, self._wp_pass, int(post_id))
                for f in post_atual.get("custom_fields") or []:
                    key = f.get("key")
                    if key and key not in existentes:
                        existentes[key] = f.get("id")
            except Exception as e:
                logger.warning(f"Falha ao ler metas existentes via XML-RPC: {e}")

            custom_fields = []
            for k, v in mapeamento.items():
                if not v:
                    continue
                field = {"key": k, "value": v}
                if existentes.get(k):
                    field["id"] = existentes[k]
                custom_fields.append(field)
            termos_nomes = {}
            bairro = str(dados.get("bairro_extraido") or "").strip()
            cidade = str(dados.get("cidade_extraida") or "").strip()
            cidade = re.sub(r"\s*/\s*[A-Z]{2}$", "", cidade).strip()
            if bairro or cidade:
                for termo in post_atual.get("terms") or []:
                    tax = termo.get("taxonomy")
                    nome = termo.get("name")
                    if tax in {"property_area", "property_city"} and nome:
                        termos_nomes.setdefault(tax, [])
                        if nome not in termos_nomes[tax]:
                            termos_nomes[tax].append(nome)
                if bairro:
                    termos_nomes.setdefault("property_area", [])
                    if bairro not in termos_nomes["property_area"]:
                        termos_nomes["property_area"].append(bairro)
                if cidade:
                    termos_nomes.setdefault("property_city", [])
                    if cidade not in termos_nomes["property_city"]:
                        termos_nomes["property_city"].append(cidade)

            if not custom_fields and not termos_nomes:
                return

            post_data = {}
            if custom_fields:
                post_data["custom_fields"] = custom_fields
            if termos_nomes:
                post_data["terms_names"] = termos_nomes

            proxy.wp.editPost("1", self._wp_user, self._wp_pass, int(post_id), post_data)
            nomes = [f"{f['key'].replace('fave_','')}={f['value']}" for f in custom_fields]
            extras = []
            if nomes:
                extras.append(", ".join(nomes))
            if termos_nomes:
                extras.append(f"termos={termos_nomes}")
            logger.info(f"Meta atualizada via XML-RPC (post {post_id}): {'; '.join(extras)}")
        except Exception as e:
            logger.warning(f"Falha ao atualizar meta via XML-RPC: {e}")

    async def _publicar(self, page, agent_id: str = "", extra_campos: dict | None = None):
        """Publica o imovel diretamente, usando o botao do formulario como caminho principal."""
        try:
            try:
                await page.wait_for_selector('#save_as_draft', timeout=5000)
            except Exception:
                logger.warning("Timeout aguardando form, tentando publicar mesmo assim")

            via_botao = await self._publicar_via_botao(page)
            if via_botao.get("ok"):
                return via_botao
            logger.warning("Publicacao via botao falhou, tentando AJAX submit_property")

            extra_str = self._extra_campos_str(extra_campos)

            resultado = await page.evaluate("""
                ({agentId, extraCampos}) => new Promise((resolve) => {
                    const $form = jQuery('#submit_property_form');
                    if (!$form.length) { resolve({ok: false, erro: 'form nao encontrado'}); return; }
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    const description = editor
                        ? editor.getContent()
                        : (window._botDescricao || (document.querySelector('#prop_des') || {}).value || '');
                    const ajaxUrl = window.ajax_url || window.ajaxurl || '/wp-admin/admin-ajax.php';
                    let extraAgent = '';
                    if (agentId) {
                        extraAgent = '&fave_agents=' + encodeURIComponent(agentId)
                                   + '&fave_agent_display_option=agent_info';
                    }
                    jQuery.ajax({
                        type: 'post',
                        url: ajaxUrl,
                        dataType: 'json',
                        data: $form.serialize() + '&action=submit_property&description=' + encodeURIComponent(description) + extraAgent + extraCampos,
                        success: function(response) { resolve({ok: true, response: JSON.stringify(response)}); },
                        error: function(xhr, status, error) {
                            resolve({ok: false, erro: error, status: status, resp: xhr.responseText.substring(0, 300)});
                        }
                    });
                })
            """, {"agentId": agent_id, "extraCampos": extra_str})

            logger.info(f"Resultado AJAX submit_property: {resultado}")

            if not resultado or not resultado.get('ok'):
                # Fallback: tenta clicar no botão de submit do formulário
                logger.warning("AJAX submit_property falhou, tentando clique no botão")
                return await self._publicar_via_botao(page)

            try:
                resp = json.loads(resultado.get('response', '{}'))
                if resp.get('success') or resp.get('suc'):
                    logger.info("Imóvel publicado com sucesso via AJAX")
                    property_id = resp.get("property_id") or resp.get("prop_id") or resp.get("id")
                    url = self._montar_url_mercadoi(property_id)
                    return {"ok": True, "property_id": property_id, "url": url}
                else:
                    logger.warning(f"AJAX submit_property retornou falha: {resp} — tentando botão")
                    return await self._publicar_via_botao(page)
            except Exception:
                logger.info("AJAX publicar completou (resposta aceita)")
                return {"ok": True}

        except Exception as e:
            logger.error(f"Erro ao publicar: {e}")
            return {"ok": False}

    async def _publicar_via_botao(self, page):
        """Fallback: clica no botão de submit do formulário para publicar."""
        try:
            seletores = [
                '#add_new_property',
                'button:has-text("Enviar imóvel")',
                'button:has-text("Enviar imovel")',
                'input[type="submit"][value*="Enviar"]',
                'button[type="submit"][id*="submit"]',
                'input[type="submit"][id*="submit"]',
                '#submit-property',
                'button:has-text("Publicar")',
                'button:has-text("Submit")',
                'button:has-text("Enviar")',
            ]
            for sel in seletores:
                btn = await page.query_selector(sel)
                if btn:
                    url_antes = page.url
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    url_depois = page.url

                    # URL igual = formulário ficou na mesma página (erro de validação)
                    if url_depois == url_antes:
                        logger.warning(
                            f"Botão '{sel}' clicado mas página não redirecionou "
                            f"— erro de validação do formulário (campo obrigatório faltando?)"
                        )
                        return {"ok": False}

                    logger.info(f"Publicado via clique no botão: {sel}")
                    import re as _re

                    # Tenta extrair ID da URL resultante (wp-admin)
                    m = _re.search(r'post=(\d+)', url_depois)
                    pid = m.group(1) if m else None
                    url_mercadoi = self._montar_url_mercadoi(pid)

                    # Se não achou ID na URL, tenta encontrar link de edição na página
                    if not pid:
                        try:
                            links = await page.evaluate("""
                                () => Array.from(document.querySelectorAll('a[href]'))
                                    .map(a => a.href)
                                    .filter(h => h.includes('post=') || h.includes('/listing/') || h.includes('/imovel/'))
                            """)
                            for lnk in links:
                                m2 = _re.search(r'post=(\d+)', lnk)
                                if m2:
                                    pid = m2.group(1)
                                    url_mercadoi = self._montar_url_mercadoi(pid)
                                    break
                            # Se ainda não achou ID, usa a URL da página resultante direto
                            if not url_mercadoi:
                                url_mercadoi = url_depois
                        except Exception:
                            url_mercadoi = url_depois

                    logger.info(f"URL Mercadoi capturada: {url_mercadoi}")
                    return {"ok": True, "property_id": pid, "url": url_mercadoi}
            logger.error("Nenhum botão de publicar encontrado")
            return {"ok": False}
        except Exception as e:
            logger.error(f"Erro ao publicar via botão: {e}")
            return {"ok": False}

    def _montar_url_mercadoi(self, property_id) -> str:
        if not property_id:
            return ""
        return urljoin(self.base_url.rstrip("/") + "/", f"wp-admin/post.php?post={property_id}&action=edit")
