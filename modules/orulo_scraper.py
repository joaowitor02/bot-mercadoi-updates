"""
Scraper para empreendimentos do Orulo (orulo.com.br).

Fluxo:
- tenta ler a pagina publica via HTTP, que e rapido;
- se a pagina cair no login do Orulo, usa navegador headless com as credenciais
  do config.json;
- se a conta exigir atualizacao cadastral, retorna um motivo especifico.
"""

import os
import re
import sys
import tempfile
import html as html_lib
import asyncio
import hashlib
import json
import time
from io import BytesIO
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from modules.logger import Logger
from modules.property_types import normalizar_tipos_imovel
from modules.caracteristicas_guard import enriquecer_caracteristicas_por_texto, filtrar_caracteristicas

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow pode nao estar disponivel
    Image = None

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
except Exception:  # pragma: no cover - ambiente sem Playwright
    async_playwright = None
    PlaywrightTimeoutError = Exception

logger = Logger("orulo_scraper")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_STANDARD_URL_RE = re.compile(
    r"https?://(?:www\.)?orulo\.com\.br/(?:a/[^/\s?#]+/\d+|buildings/\d+)",
    re.IGNORECASE,
)
_SHARE_URL_RE = re.compile(
    r"https?://(?:www\.)?orulo\.com\.br/s/[^/\s?#]+/share/?(?:\?[^#\s]*)?",
    re.IGNORECASE,
)
_URL_RE = re.compile(
    r"https?://(?:www\.)?orulo\.com\.br/(?:a/[^/\s?#]+/\d+|buildings/\d+|s/[^/\s?#]+/share/?(?:\?[^#\s]*)?)",
    re.IGNORECASE,
)

_MAX_IMAGENS = 20

_TIPOS = [
    "Casa em Condominio", "Casa de Condominio", "Casa Condomínio",
    "Casa Duplex", "Casa Sobrado", "Casa", "Sobrado",
    "Terrenos", "Terreno", "Lotes", "Lote",
    "Apartamento Cobertura", "Apartamento Duplex", "Apartamento Garden",
    "Apartamento Studio", "Apto. Cobertura", "Apto. Duplex", "Apto. Garden", "Apto. Studio",
    "Cobertura Duplex", "Duplex Cobertura", "Cobertura", "Duplex",
    "Apartamento", "Garden", "Studio", "Flat",
    "Sala Comercial", "Loja", "Galpao",
]

_AMENIDADES = [
    # A ordem importa: termos mais específicos primeiro para evitar matches duplos
    ("Piscina infantil",  ["piscina infantil", "piscina kids"]),
    ("Piscina adulto",    ["piscina"]),          # pega qualquer "piscina" restante
    ("Academia",          ["academia", "fitness"]),
    ("Salão de festas",   ["salao de festas", "salão de festas"]),
    ("Espaço gourmet",    ["espaco gourmet", "espaço gourmet", "gourmet"]),
    ("Churrasqueira",     ["churrasqueira"]),
    ("Playground",        ["playground"]),
    ("Brinquedoteca",     ["brinquedoteca"]),
    ("Lounge",            ["lounge", "louge"]),
    ("Coworking",         ["coworking"]),
    ("Pet place",         ["pet place", "pet care"]),
    ("Lavanderia",        ["lavanderia"]),
    ("Mini mercado",      ["mini mercado", "minimercado", "mini market"]),
    ("Hidromassagem",     ["hidromassagem"]),
    ("Segurança",         ["seguranca", "segurança"]),
    ("Bicicletario",      ["bicicletario", "bicicletário"]),
    ("Portaria 24h",      ["portaria 24h", "portaria 24 horas", "portaria 24"]),
    ("Portaria",          ["portaria"]),
    ("Elevador",          ["elevador"]),
    ("Quadra",            ["quadra"]),
    ("Rooftop",           ["rooftop"]),
    ("Sauna",             ["sauna"]),
    ("Solarium",          ["solarium", "solário"]),
    ("Deck",              ["deck"]),
    ("Sala de jogos",     ["sala de jogos", "salao de jogos", "salão de jogos"]),
    ("Cinema",            ["cinema"]),
    ("Espaco zen",        ["espaco zen", "espaço zen", "jardim zen"]),
]

# Amenidades que nunca devem aparecer nas características (política do cliente).
# Deve ser espelho de mercadoi_driver._NUNCA_MARCAR para bloquear na origem.
_AMENIDADES_EXCLUIR = {
    "banheiro social",
    "biblioteca",
    "circuito de seguranca", "camera de seguranca",
    # "piscina adulto" → OK marcar no Orulo: "piscina" sem qualificador = adulto
    "piscina infantil", "piscina kids",
    "piscina privativa",
    "portaria 24h", "portaria 24 horas", "portaria eletronica",
    "deck", "deck molhado",
    # "solarium" → OK marcar (confirmado nos screenshots do cliente)
    "spa",
    "terraco", "terraco rooftop", "rooftop",
    "sistema de alarme",
    "seguranca 24h", "seguranca 24 horas", "seguranca eletronica",
    "segurança 24h", "segurança eletrônica",
}

_TERMOS_PLANTA = [
    "planta", "plantas", "floorplan", "floor plan", "floor_plan",
    "blueprint", "unit plan", "unit_plan", "implantacao", "implantaÃ§Ã£o",
    "layout", "typology", "tipologia", "pavimento", "pavimentos",
]

_TERMOS_GALERIA = [
    "ver fotos", "galeria", "gallery", "photos", "photo", "foto", "fotos",
    "carousel", "fachada", "facade", "building", "empreendimento", "banner",
]

_ORULO_CLIENT_ID = "TKYZFTALAu1tshVidMJdV15BKz97ghBs_xaoarFpCiY"
_ORULO_RETURN_TO = (
    "https://www.orulo.com.br/oauth/authorize"
    f"?client_id={_ORULO_CLIENT_ID}"
    "&redirect_uri=https%3A%2F%2Fwww.orulo.com.br%2Fcustomers%2Foauth2%2Fcallback"
    "&response_type=code"
)


def url_valida(url: str) -> bool:
    return bool(_URL_RE.match(url.strip()))


def normalizar_url(url: str) -> str:
    """Normaliza URLs do Orulo preservando jwt em links compartilhados."""
    raw = url.strip()
    m_share = _SHARE_URL_RE.search(raw)
    if m_share:
        parsed = urlsplit(m_share.group(0))
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k == "jwt"]
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), query, ""))

    m = _STANDARD_URL_RE.search(raw)
    return m.group(0).rstrip("/") if m else raw


class OruloScraper:
    def __init__(
        self,
        email: str = "",
        senha: str = "",
        profile_path: str = "",
        gallery_cache_ttl_horas: int = 12,
    ):
        self.email = (email or "").strip()
        self.senha = (senha or "").strip()
        self.profile_path = (
            profile_path
            or os.path.join(tempfile.gettempdir(), "orulo_browser_profile")
        )
        self.gallery_cache_ttl_horas = max(0, int(gallery_cache_ttl_horas or 0))

    async def extrair(self, url: str) -> dict:
        """Scrapa pagina de empreendimento e retorna dados + imagens."""
        url = normalizar_url(url)
        if not url_valida(url):
            return {"ok": False, "motivo": "url_invalida"}

        html = None
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers=_HEADERS,
            ) as client:
                r = await client.get(url)
                if self._pagina_login(str(r.url), r.text):
                    logger.info("Orulo: pagina exige login")
                else:
                    html = r.text
        except httpx.TimeoutException:
            logger.warning(f"Orulo: timeout ao acessar {url}")
            return {"ok": False, "motivo": "erro_rede"}
        except Exception as e:
            logger.error(f"Orulo: erro de rede em {url}: {e}")
            return {"ok": False, "motivo": "erro_rede"}

        if html is None:
            if r.status_code == 404:
                return {"ok": False, "motivo": "nao_encontrado"}
            if r.status_code != 200:
                logger.error(f"Orulo: HTTP {r.status_code} para {url}")
                return {"ok": False, "motivo": "erro_rede"}

        dados = {}
        imagens_urls = []
        if html:
            dados = self._extrair_dados(html, url)
            imagens_urls = self._extrair_imagens(html)

        # Aciona Playwright autenticado se: sem título OU título é tipo genérico
        # ("Apartamento", "Casa"…) que indica que o JS não foi renderizado.
        # O título extraído é cacheado para evitar Playwright a cada reprocessamento.
        _TITULO_GENERICO = {"apartamento", "casa", "terreno", "sala", "sala comercial",
                            "flat", "studio", "kitnet", "imovel", "imóvel", "cobertura"}
        _titulo_atual = (dados.get("titulo") or "").strip().lower()
        _precisa_auth = (not _titulo_atual) or (_titulo_atual in _TITULO_GENERICO)

        if _precisa_auth:
            titulo_cache = self._get_titulo_cache(url)
            if titulo_cache:
                logger.info(f"Orulo: título do cache → '{titulo_cache}'")
                dados["titulo"] = titulo_cache
                # Titulo em cache sozinho nao substitui a pagina autenticada.
                # Quando a resposta publica e a tela de login, `dados` fica sem
                # preco/tipologia e o fluxo precisa abrir a Orulo logada.
                if html and (dados.get("preco") or dados.get("_dados_variacoes")):
                    _precisa_auth = False

        if _precisa_auth and self.email and self.senha:
            logger.info(f"Orulo: titulo genérico/ausente ('{_titulo_atual}') — tentando leitura autenticada")
            auth = await self._fetch_autenticado(url)
            if not auth.get("ok"):
                return auth
            html = auth["html"]
            dados = self._extrair_dados(html, url)
            imagens_urls = self._extrair_imagens(html)
            # Cacheia título para evitar Playwright no próximo processamento
            if dados.get("titulo") and dados["titulo"].lower() not in _TITULO_GENERICO:
                self._set_titulo_cache(url, dados["titulo"])
        elif _precisa_auth:
            return {"ok": False, "motivo": "login_necessario"}

        if dados.get("titulo") and len(imagens_urls) < _MAX_IMAGENS:
            galeria_urls = self._obter_cache_galeria(url)
            if galeria_urls:
                logger.info(f"Orulo: galeria em cache com {len(galeria_urls)} foto(s)")
            else:
                galeria_urls = await self._fetch_galeria_imagens(url)
                if galeria_urls:
                    self._salvar_cache_galeria(url, galeria_urls)
            if galeria_urls:
                imagens_urls = self._deduplicar_urls_imagem(
                    galeria_urls + imagens_urls
                )[:_MAX_IMAGENS]

        if not dados.get("titulo"):
            return {"ok": False, "motivo": "estrutura_desconhecida"}

        dados_variacoes = dados.pop("_dados_variacoes", [])

        logger.info(
            f"Orulo: '{dados['titulo'][:70]}' | preco={dados.get('preco', '')} | "
            f"{len(imagens_urls)} foto(s) | {len(dados_variacoes) or 1} publicacao(oes)"
        )
        return {
            "ok": True,
            "dados": dados,
            "dados_variacoes": dados_variacoes,
            "imagens_urls": imagens_urls,
        }

    async def baixar_imagens(self, urls: list, downloads_path: str) -> list:
        """Baixa ate 20 imagens em paralelo e remove repetidas por conteudo."""
        os.makedirs(downloads_path, exist_ok=True)
        urls = self._deduplicar_urls_imagem(urls)[:_MAX_IMAGENS]
        caminhos = []
        vistos_hash = set()
        semaforo = asyncio.Semaphore(8)

        async def baixar(client, idx: int, img_url: str):
            async with semaforo:
                try:
                    r = await client.get(img_url)
                    if r.status_code != 200 or not r.content:
                        return None
                    digest = hashlib.sha1(r.content).hexdigest()
                    visual_hash = self._imagem_hash_visual(r.content)
                    ext = img_url.split(".")[-1].split("?")[0].lower()
                    ext = ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"
                    return idx, img_url, r.content, digest, visual_hash, ext
                except Exception as e:
                    logger.warning(f"Orulo: falha ao baixar imagem {img_url[:60]}: {e}")
                    return None

        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers=_HEADERS
        ) as client:
            resultados = await asyncio.gather(
                *[baixar(client, i, img_url) for i, img_url in enumerate(urls, 1)]
            )

        ordem_salva = 1
        vistos_visuais = []
        for item in sorted([r for r in resultados if r], key=lambda r: r[0]):
            _, _, conteudo, digest, visual_hash, ext = item
            if digest in vistos_hash:
                continue
            if visual_hash and any(self._hash_parecido(visual_hash, h) for h in vistos_visuais):
                continue
            vistos_hash.add(digest)
            if visual_hash:
                vistos_visuais.append(visual_hash)
            nome = f"orulo_{digest[:10]}_{ordem_salva:02d}.{ext}"
            caminho = os.path.join(downloads_path, nome)
            with open(caminho, "wb") as fh:
                fh.write(conteudo)
            caminhos.append(caminho)
            ordem_salva += 1
            logger.info(f"Orulo: baixado {nome} ({len(conteudo) // 1024} KB)")
        return caminhos

    async def _fetch_autenticado(self, url: str) -> dict:
        if async_playwright is None:
            return {"ok": False, "motivo": "erro_rede"}

        os.makedirs(self.profile_path, exist_ok=True)
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_path,
                    headless=True,
                    viewport={"width": 1365, "height": 900},
                    user_agent=_HEADERS["User-Agent"],
                    locale="pt-BR",
                    args=["--no-sandbox", "--disable-dev-shm-usage"] if sys.platform != "win32" else [],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    html = await self._abrir_logado(page, url)
                finally:
                    await context.close()
        except Exception as e:
            logger.error(f"Orulo: falha no login/leitura autenticada: {e}")
            return {"ok": False, "motivo": "erro_rede"}

        if html == "cadastro_incompleto":
            return {"ok": False, "motivo": "cadastro_orulo_incompleto"}
        if not html:
            return {"ok": False, "motivo": "acesso_restrito"}
        return {"ok": True, "html": html}

    # ------------------------------------------------------------------
    # Cache de título (evita Playwright a cada reprocessamento)
    # ------------------------------------------------------------------
    _TITULO_CACHE_TTL = 7 * 24 * 3600  # 7 dias

    def _titulo_cache_path(self) -> str:
        return os.path.join(self.profile_path, "orulo_titulo_cache.json")

    def _get_titulo_cache(self, url: str) -> str:
        try:
            path = self._titulo_cache_path()
            if not os.path.exists(path):
                return ""
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            key = hashlib.sha1(normalizar_url(url).encode("utf-8", errors="ignore")).hexdigest()
            item = cache.get(key) or {}
            if not item or time.time() - float(item.get("ts", 0)) > self._TITULO_CACHE_TTL:
                return ""
            return item.get("titulo", "")
        except Exception:
            return ""

    def _set_titulo_cache(self, url: str, titulo: str) -> None:
        try:
            path = self._titulo_cache_path()
            cache = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            key = hashlib.sha1(normalizar_url(url).encode("utf-8", errors="ignore")).hexdigest()
            cache[key] = {"titulo": titulo, "ts": time.time()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _gallery_cache_path(self) -> str:
        return os.path.join(self.profile_path, "orulo_gallery_cache.json")

    def _gallery_cache_key(self, url: str) -> str:
        return hashlib.sha1(normalizar_url(url).encode("utf-8", errors="ignore")).hexdigest()

    def _obter_cache_galeria(self, url: str) -> list:
        if self.gallery_cache_ttl_horas <= 0:
            return []
        path = self._gallery_cache_path()
        try:
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as fh:
                cache = json.load(fh)
            item = cache.get(self._gallery_cache_key(url)) or {}
            if not item:
                return []
            idade = time.time() - float(item.get("ts") or 0)
            if idade > self.gallery_cache_ttl_horas * 3600:
                return []
            urls = item.get("urls") or []
            return self._deduplicar_urls_imagem(urls)[:_MAX_IMAGENS]
        except Exception:
            return []

    def _salvar_cache_galeria(self, url: str, urls: list) -> None:
        if self.gallery_cache_ttl_horas <= 0 or not urls:
            return
        try:
            os.makedirs(self.profile_path, exist_ok=True)
            path = self._gallery_cache_path()
            cache = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        cache = json.load(fh)
                except Exception:
                    cache = {}
            cache[self._gallery_cache_key(url)] = {
                "ts": time.time(),
                "urls": self._deduplicar_urls_imagem(urls)[:_MAX_IMAGENS],
            }
            limite = time.time() - (max(self.gallery_cache_ttl_horas, 1) * 3600 * 4)
            cache = {
                k: v for k, v in cache.items()
                if float((v or {}).get("ts") or 0) >= limite
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Orulo: falha ao salvar cache da galeria: {e}")

    async def _fetch_galeria_imagens(self, url: str) -> list:
        """Abre a galeria "Ver fotos" e coleta imagens carregadas no modal."""
        if async_playwright is None:
            return []

        os.makedirs(self.profile_path, exist_ok=True)
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_path,
                    headless=True,
                    viewport={"width": 1365, "height": 900},
                    user_agent=_HEADERS["User-Agent"],
                    locale="pt-BR",
                    args=["--no-sandbox", "--disable-dev-shm-usage"] if sys.platform != "win32" else [],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    await self._networkidle(page)

                    html = await page.content()
                    if self._pagina_login(page.url, html):
                        if not (self.email and self.senha):
                            return []
                        await self._login(page)
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        await self._networkidle(page)

                    if self._cadastro_incompleto(page.url, await page.title(), await page.content()):
                        return []

                    if not await self._abrir_modal_fotos(page):
                        logger.info("Orulo: botao 'Ver fotos' nao encontrado; usando fotos iniciais")
                        return []

                    urls = await self._coletar_urls_imagens_page(page)
                    urls = self._filtrar_urls_imagens(urls)
                    if urls:
                        logger.info(f"Orulo: galeria expandida com {len(urls)} foto(s)")
                    return urls
                finally:
                    await context.close()
        except Exception as e:
            logger.warning(f"Orulo: falha ao abrir galeria de fotos: {e}")
            return []

    async def _abrir_modal_fotos(self, page) -> bool:
        seletores = [
            'text=/Ver\\s+fotos/i',
            'button:has-text("Ver fotos")',
            'a:has-text("Ver fotos")',
            '[aria-label*="foto" i]',
            '[class*="gallery" i]',
        ]
        for sel in seletores:
            try:
                loc = page.locator(sel).first
                if await loc.count():
                    await loc.click(timeout=5000)
                    await page.wait_for_timeout(1500)
                    await self._networkidle(page)
                    return True
            except Exception:
                continue
        return False

    async def _coletar_urls_imagens_page(self, page) -> list:
        return await page.evaluate(r"""
            () => {
                const urls = [];
                const add = (value) => {
                    if (!value) return;
                    String(value).split(',').forEach(part => {
                        const raw = part.trim().split(/\s+/)[0];
                        if (raw) urls.push(raw);
                    });
                };
                document.querySelectorAll('img, source').forEach(el => {
                    add(el.currentSrc || el.src || el.getAttribute('src'));
                    add(el.getAttribute('srcset'));
                    add(el.getAttribute('data-src'));
                    add(el.getAttribute('data-srcset'));
                    add(el.getAttribute('data-original'));
                });
                document.querySelectorAll('[style]').forEach(el => {
                    const style = el.getAttribute('style') || '';
                    const matches = style.matchAll(/url\(["']?([^"')]+)["']?\)/g);
                    for (const m of matches) add(m[1]);
                });
                return urls;
            }
        """)

    async def _abrir_logado(self, page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self._networkidle(page)

        html = await page.content()
        if self._pagina_login(page.url, html):
            await self._login(page)

        html = await page.content()
        if self._cadastro_incompleto(page.url, await page.title(), html):
            logger.warning("Orulo: conta exige atualizacao de cadastro antes de liberar acesso")
            return "cadastro_incompleto"

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self._networkidle(page)

        html = await page.content()
        if self._pagina_login(page.url, html):
            return ""
        if self._cadastro_incompleto(page.url, await page.title(), html):
            return "cadastro_incompleto"

        try:
            texto = await page.locator("body").inner_text(timeout=10000)
        except Exception:
            texto = ""
        return html + "\n" + texto

    async def _login(self, page) -> None:
        login_url = (
            "https://auth.orulo.com.br/email"
            f"?client_id={_ORULO_CLIENT_ID}"
            f"&return_to={quote(_ORULO_RETURN_TO, safe='')}"
        )
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        await page.fill('input[name="email"]', self.email)
        await page.fill('input[name="password"]', self.senha)
        await page.click('button[type="submit"]')
        await self._networkidle(page)

    async def _networkidle(self, page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass

    def _pagina_login(self, url: str, html: str) -> bool:
        return (
            "auth.orulo.com.br" in url
            or "customers/sign_in" in url
            or "<title>Login - " in html
            or "Continuar com e-mail" in html
        )

    def _cadastro_incompleto(self, url: str, title: str, html: str) -> bool:
        return (
            "forced_update=true" in url
            or "customers/edit" in url
            or "Atualizacao de Cadastro" in html
            or "Atualização de Cadastro" in html
            or "Edição de cadastro" in title
        )

    # ------------------------------------------------------------------
    # Extracao de campos do HTML/texto
    # ------------------------------------------------------------------

    def _extrair_dados(self, html: str, url: str) -> dict:
        og_title = self._meta(html, "og:title")
        og_desc = self._meta(html, "og:description") or self._meta(html, "description")

        tipo_imovel, endereco, bairro, cidade = self._parse_og_title(og_title)
        # Fallback: tenta extrair endereço do HTML quando og:title não trouxe rua
        if not endereco:
            endereco = self._extrair_endereco_html(html)
        tipos_imovel_lista = self._tipos_orulo(tipo_imovel, og_title)
        tipo_imovel = tipos_imovel_lista[0] if tipos_imovel_lista else "Apartamento"

        nome_emp = self._nome_empreendimento(html)
        # Título: nome do empreendimento. Se _nome_empreendimento falhou, tenta
        # _titulo_texto (h1, JSON-LD) antes de cair no og_title genérico.
        # og_title sozinho ("Apartamento") não é útil como título de empreendimento.
        _TIPOS_GENERICOS = {"apartamento", "casa", "terreno", "sala", "sala comercial",
                            "cobertura", "flat", "studio", "kitnet", "imovel", "imóvel"}
        og_titulo_util = og_title if og_title and og_title.strip().lower() not in _TIPOS_GENERICOS else ""
        titulo = nome_emp or self._titulo_texto(html) or og_titulo_util
        if self._mesmo_texto(bairro, nome_emp or titulo):
            bairro = ""
        endereco, bairro_extraido = self._separar_endereco_bairro(endereco, bairro)
        if bairro_extraido:
            bairro = bairro_extraido

        tipologias = self._extrair_tipologias(html, tipo_imovel)
        tipologia_base = tipologias[0] if tipologias else {}

        def _tipologia_ou_fallback(campo: str, fallback: str) -> str:
            if campo in tipologia_base and tipologia_base.get(campo) is not None:
                return tipologia_base.get(campo) or ""
            return fallback

        preco = _tipologia_ou_fallback("preco", self._extrair_preco(html))
        area = _tipologia_ou_fallback("area_m2", self._extrair_num_range(html, r"m[²2]"))
        quartos = _tipologia_ou_fallback("quartos", self._extrair_num_range(html, r"quarto"))
        suites = _tipologia_ou_fallback("suites", self._extrair_num_range(html, r"su[ií]te"))
        banheiros = _tipologia_ou_fallback("banheiros", self._extrair_num_range(html, r"banheiro"))
        vagas = _tipologia_ou_fallback("vagas", self._extrair_num_range(html, r"vaga"))

        resumo = self._resumo_tipologias(tipologias, html)
        caracteristicas = self._extrair_caracteristicas(html)
        resumo_empreendimento = self._resumo_empreendimento(html, caracteristicas)
        # Descrição: apenas áreas comuns + tipologias (sem og:description para evitar
        # que a IA considere texto de marketing da Orulo como informação estruturada)
        descricao = "\n\n".join(p for p in [resumo_empreendimento, resumo] if p).strip()
        latitude, longitude = self._extrair_coordenadas(html)
        cep = self._extrair_cep(html)

        estagio = self._extrair_estagio(html)
        elevador = self._extrair_elevador(html, caracteristicas)
        ano_construcao = self._extrair_ano_construcao(html)
        condominio = self._extrair_condominio(html)
        perto_do_mar = self._extrair_perto_do_mar(bairro, cidade, html)
        info_adicional = self._extrair_info_adicional(html)
        if not estagio:
            estagio = self._inferir_estagio_por_info(info_adicional)

        dados = {
            "titulo": titulo,
            "descricao_util": descricao,
            "tipo_imovel": tipo_imovel,
            "tipo_imovel_lista": tipos_imovel_lista,
            "operacao": "A Venda",
            "preco": preco,
            "quartos": quartos,
            "suites": suites,
            "banheiros": banheiros,
            "vagas": vagas,
            "area_m2": area,
            "area_terreno": "",
            "ano_construcao": ano_construcao,
            "condominio": condominio,
            "andar": "",
            "elevador": elevador,
            "estagio_imovel": estagio,
            "perto_do_mar": perto_do_mar,
            "endereco": endereco,
            "cep": cep,
            "latitude": latitude,
            "longitude": longitude,
            "bairro_extraido": bairro,
            "cidade_extraida": cidade,
            "url_publicacao": url,
            "whatsapp_url": "",
            "instagram_url": "",
            "caracteristicas": caracteristicas,
            "_tipologias_resumo": resumo,
            "_empreendimento_resumo": resumo_empreendimento,
            "_info_adicional": info_adicional,
        }
        dados = enriquecer_caracteristicas_por_texto(dados)
        if tipologias:
            dados["_dados_variacoes"] = [
                self._montar_dados_tipologia(dados, t, i + 1, len(tipologias))
                for i, t in enumerate(tipologias)
            ]
        return dados

    def _inferir_estagio_por_info(self, info: dict) -> str:
        bruto = str((info or {}).get("estagio") or "").strip()
        n = self._normalizar_texto(bruto)
        if any(p in n for p in ["construcao", "obra", "obras"]):
            return "Em Construção"
        if any(p in n for p in ["pronto", "entregue"]):
            return "Novo"
        if any(p in n for p in ["lancamento", "breve"]):
            return "Em Construção"
        if (info or {}).get("entrega") or (info or {}).get("lancamento"):
            return "Em Construção"
        return ""

    # ------------------------------------------------------------------
    # Helpers de parsing
    # ------------------------------------------------------------------

    def _meta(self, html: str, name: str) -> str:
        """Extrai conteudo de meta tag por property ou name."""
        prop = name if ":" in name else None
        tag_name = name if ":" not in name else None
        patterns = []
        if prop:
            patterns += [
                rf'<meta[^>]+property=["\'](?:og:)?{re.escape(prop.split(":")[-1])}["\'][^>]+content=["\']([^"\']+)["\']',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:og:)?{re.escape(prop.split(":")[-1])}["\']',
            ]
        if tag_name:
            patterns += [
                rf'<meta[^>]+name=["\'](?:og:)?{re.escape(tag_name)}["\'][^>]+content=["\']([^"\']+)["\']',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\'](?:og:)?{re.escape(tag_name)}["\']',
            ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    def _parse_og_title(self, og_title: str) -> tuple:
        if not og_title:
            return "", "", "", ""

        text = og_title
        tipo = ""
        for t in _TIPOS:
            if text.lower().startswith(t.lower()):
                tipo = t
                text = text[len(t):].lstrip(" -")
                break

        cidade = ""
        m = re.search(r"([A-ZÀ-Ú][a-zA-ZÀ-ú\s]+)/([A-Z]{2})", text)
        if m:
            cidade = f"{m.group(1).strip()}/{m.group(2)}"
            text = (text[:m.start()] + text[m.end():]).strip(" -")

        endereco, bairro = "", ""
        # Divide pelas partes separadas por " - " ou " – "
        partes = re.split(r"\s*[-–]\s*", text)
        partes = [p.strip() for p in partes if p.strip()]
        if len(partes) >= 2:
            # Heurística: parte com "Av", "R.", "Rua", "Avenida", número → endereço
            for i, p in enumerate(partes):
                if re.search(r"\b(av|rua|r\.|avenida|estrada|rod|rodovia|travessa|alameda|pca|praça)\b", p, re.IGNORECASE) \
                   or re.search(r"\d+", p):
                    endereco = p
                    restantes = [x for j, x in enumerate(partes) if j != i]
                    bairro = restantes[0] if restantes else ""
                    break
            if not endereco:
                bairro = partes[-1]
        elif partes:
            bairro = partes[0]

        return tipo, endereco, bairro, cidade

    def _extrair_endereco_html(self, html: str) -> str:
        """Extrai rua/endereço do HTML quando og:title não trouxe."""
        for pat in [
            r'"streetAddress"\s*:\s*"([^"]{5,80})"',
            r'"address"\s*:\s*"([^"]{5,80})"',
            r'logradouro["\s:=]+([^"<\n]{5,60})',
            r'(?:Rua|Av\.|Avenida|Estrada|Rod\.)\s+[^<\n,]{5,60}',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                val = m.group(1) if m.lastindex else m.group(0)
                val = val.strip().strip('"')
                if len(val) >= 5:
                    return val
        return ""

    def _separar_endereco_bairro(self, endereco: str, bairro: str = "") -> tuple[str, str]:
        endereco = (endereco or "").strip()
        bairro = (bairro or "").strip().strip(" ,.-")
        if not endereco:
            return "", bairro

        # Ex.: "Rua Cordélia Velloso Frade 1240. Jardim Cidade Universitária".
        m = re.match(r"^(.+?\b\d+[A-Za-zºª\-\/]*)[.,;]\s+(.{3,60})$", endereco)
        if m:
            endereco = m.group(1).strip()
            bairro_do_endereco = m.group(2).strip().strip(" ,.-")
            if not bairro:
                bairro = bairro_do_endereco

        if self._bairro_parece_endereco(bairro):
            bairro = ""
        return endereco, bairro

    def _bairro_parece_endereco(self, valor: str) -> bool:
        valor = (valor or "").strip()
        if not valor:
            return False
        return bool(
            re.search(r"\b(rua|av\.?|avenida|estrada|rodovia|travessa|alameda)\b", valor, re.IGNORECASE)
            or re.search(r"\d{2,}", valor)
        )

    def _mesmo_texto(self, a: str, b: str) -> bool:
        if not a or not b:
            return False
        na = self._normalizar_texto(a)
        nb = self._normalizar_texto(b)
        return na == nb or na in nb or nb in na

    def _nome_empreendimento(self, html: str) -> str:
        def _valido(n: str) -> bool:
            return bool(n) and len(n) >= 5 and "Login" not in n and "Orulo" not in n

        # 1. <title>Nome | Orulo</title>  (formato mais comum do Orulo)
        m = re.search(
            r"<title>\s*([^|<]{5,120}?)\s*\|\s*(?:Orulo|Lançamentos|Imoveis)[^<]*</title>",
            html, re.IGNORECASE,
        )
        if m and _valido(m.group(1).strip()):
            return m.group(1).strip()

        # 2. og:title com pipe: "Nome | Orulo"
        og = self._meta(html, "og:title") or ""
        if "|" in og:
            partes = [p.strip() for p in og.split("|")]
            for parte in partes:
                if _valido(parte) and parte.lower() not in {"orulo", "apartamento", "casa"}:
                    return parte

        # 3. JSON-LD: "name": "..."
        for m in re.finditer(r'"name"\s*:\s*"([^"]{5,120})"', html):
            nome = m.group(1).strip()
            if _valido(nome) and nome[0].isupper():
                return nome

        # 4. Atributo data-name ou similar com nome do prédio
        m = re.search(r'data-(?:building-name|name|title)=["\']([^"\']{5,120})["\']', html, re.IGNORECASE)
        if m and _valido(m.group(1).strip()):
            return m.group(1).strip()

        # 5. Fallback: variável JS
        m = re.search(r'building_name\s*=\s*["\']([^"\']+)["\']', html)
        if m and _valido(m.group(1).strip()):
            return m.group(1).strip()

        # 6. <title> invertido (Orulo | Nome)
        m = re.search(r"<title>[^|<]*\|\s*([^<]{5,120})</title>", html, re.IGNORECASE)
        if m:
            nome = re.split(r"\s*[-–]\s*[A-ZÀ-Ú]", m.group(1).strip())[0].strip()
            if _valido(nome):
                return nome

        return ""

    def _titulo_texto(self, html: str) -> str:
        padroes = [
            # <h1> com classe relacionada a nome do empreendimento
            r'<h1[^>]*class="[^"]*(?:building|name|title|heading|empreend)[^"]*"[^>]*>\s*([^<]{5,120})\s*</h1>',
            # <h1> genérico
            r"<h1[^>]*>\s*([^<]{5,120})\s*</h1>",
            # Padrão "Empreendimento NomePredio" no texto visível
            r"(?:Empreendimento|Condominio|Condomínio)\s+([A-Z][^\n\r]{5,90})",
            # Classe específica Orulo para nome do empreendimento
            r'class="[^"]*(?:building|empreend)[^"]*-name[^"]*"[^>]*>\s*([^<]{5,120})\s*<',
        ]
        for pat in padroes:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                nome = re.sub(r"\s+", " ", m.group(1)).strip()
                if len(nome) >= 5 and "Login" not in nome:
                    return nome
        return ""

    def _extrair_preco(self, html: str) -> str:
        m = re.search(r'class="[^"]*m-price[^"]*"[^>]*>\s*R\$\s*([\d.,]+)', html, re.IGNORECASE)
        if not m:
            m = re.search(r"R\$\s*([\d]{1,3}(?:[.,]\d{3})+)", html)
        if m:
            raw = re.sub(r"[^\d]", "", m.group(1))
            if 4 <= len(raw) <= 9:
                return raw
        return ""

    def _extrair_num_range(self, html: str, label: str) -> str:
        pat = (
            r'class=["\'][^"\']*list_value[^"\']*["\'][^>]*>'
            r"\s*([\d]+(?:\s*a\s*[\d]+)?)\s*</div>"
            r"\s*<div[^>]*>\s*" + label
        )
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+)(?:\s*a\s*\d+)?\s*" + label, html, re.IGNORECASE)
        if m:
            nums = re.findall(r"\d+", m.group(1))
            if nums and int(nums[0]) > 0:
                return nums[0]
        return ""

    def _extrair_estagio(self, html: str) -> str:
        html_low = html.lower()
        if any(p in html_low for p in ["lancamento", "lançamento", "pre-lancamento", "pre-lançamento"]):
            return "Em Construção"
        if any(p in html_low for p in ["pronto para morar", "pronto pra morar", "entregue"]):
            return "Novo"
        if any(p in html_low for p in ["em obras", "em construcao", "em construção"]):
            return "Em Construção"
        return ""

    def _extrair_elevador(self, html: str, caracteristicas: list) -> str:
        nomes_car = [str(c).lower() for c in (caracteristicas or [])]
        if any("elevador" in c for c in nomes_car):
            return "Sim"
        html_low = html.lower()
        if "sem elevador" in html_low or "nao possui elevador" in html_low or "não possui elevador" in html_low:
            return "Não"
        if re.search(r"\belevador\b", html_low):
            return "Sim"
        return ""

    def _extrair_ano_construcao(self, html: str) -> str:
        padroes = [
            r"previs[aã]o\s+de\s+entrega[:\s]+(?:(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[^\d]*)?(\d{4})",
            r"entrega\s+(?:prevista\s+)?(?:para\s+)?(?:\d+[ºo°]?\s+trim\w*\s+(?:de\s+)?)?(\d{4})",
            r"ano\s+de\s+constru[çc][aã]o[:\s]+(\d{4})",
            r"constru[íi]do\s+em\s+(\d{4})",
            r"conclu[íi]do\s+em\s+(\d{4})",
            r"inaugurado\s+em\s+(\d{4})",
            r"entregue\s+em\s+(\d{4})",
            r'"yearBuilt"\s*:\s*"?(\d{4})"?',
            r'"year_built"\s*:\s*"?(\d{4})"?',
        ]
        for pat in padroes:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                ano = int(m.group(1))
                if 1950 <= ano <= 2040:
                    return str(ano)
        return ""

    def _extrair_condominio(self, html: str) -> str:
        padroes = [
            r"condom[íi]nio[:\s]+R?\$?\s*([\d.,]+)",
            r"taxa\s+(?:de\s+)?condom[íi]nio[:\s]+R?\$?\s*([\d.,]+)",
            r'"condominium_fee"\s*:\s*"?([\d.,]+)"?',
        ]
        for pat in padroes:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                valor = re.sub(r"[.,](?=\d{3})", "", m.group(1)).replace(",", ".")
                try:
                    v = float(valor)
                    if 50 <= v <= 50000:
                        return str(int(v))
                except ValueError:
                    pass
        return ""

    def _extrair_info_adicional(self, html: str) -> dict:
        """Extrai campos VERDES da seção 'Outras informações' do Orulo.

        Incluídos: Estágio, Estoque, Lançamento, Entrega, Unidades por andar,
                   Total de unidades, Número de andares.
        Excluídos (nunca extrair): Área da Laje, Área do Terreno, Condomínio,
                   Atualizado em, IRI, PTU.
        """
        texto = self._texto_visivel(html)
        info = {}

        def _buscar(padroes):
            for pat in padroes:
                m = re.search(pat, texto, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            return ""

        info["estoque"] = _buscar([
            r"estoque\s*(?:disponivel|disponível)?\s*:?\s*(\d+)",
            r"(\d+)\s+unidades?\s+disponiv",
        ])
        info["lancamento"] = _buscar([
            r"lan[cç]amento\s*:?\s*([\d/\-\.]{5,10})",
            r"data\s+de\s+lan[cç]amento\s*:?\s*([\d/\-\.]{5,10})",
        ])
        info["estagio"] = _buscar([
            r"est[aá]gio\s*:?\s*([A-Za-zÀ-ú ]{3,40})",
            r"status\s+da\s+obra\s*:?\s*([A-Za-zÀ-ú ]{3,40})",
            r"est[aá]gio\s+da\s+obra\s*:?\s*([A-Za-zÀ-ú ]{3,40})",
        ])
        info["entrega"] = _buscar([
            r"entrega\s*:?\s*([\d/\-\.]{5,10})",
            r"previs[aã]o\s+de\s+entrega\s*:?\s*([\d/\-\.]{5,10})",
            r"entregue?\s+em\s*:?\s*([\d/\-\.]{5,10})",
        ])
        info["total_unidades"] = _buscar([
            r"total\s+de\s+unidades\s*:?\s*(\d+)",
            r"(\d+)\s+unidades?\s+(?:no\s+total|totais)",
        ])
        info["unidades_por_andar"] = _buscar([
            r"unidades?\s+por\s+andar\s*:?\s*(\d+)",
            r"(\d+)\s+unidades?\s+por\s+andar",
        ])
        # Padrão específico para evitar falso positivo com "total de unidades" etc.
        info["andares"] = _buscar([
            r"n[uú]mero\s+de\s+andares\s*:?\s*(\d+)",
            r"(\d+)\s+pavimentos?\b",
        ])
        # Remove campos vazios
        return {k: v for k, v in info.items() if v}

    def _extrair_perto_do_mar(self, bairro: str, cidade: str, html: str) -> str:
        texto = (bairro + " " + cidade + " " + html[:3000]).lower()
        if any(p in texto for p in ["frente ao mar", "beira-mar", "beira mar", "frente para o mar"]):
            return "Frente para o mar"
        if any(p in texto for p in ["vista para o mar", "vista mar", "vista ao mar"]):
            return "Vista para o mar"
        if any(p in texto for p in ["quadra do mar", "quadra mar", "a quadra do mar"]):
            return "Quadra do mar"
        if any(p in texto for p in ["perto do mar", "proximo ao mar", "próximo ao mar", "próximo a praia",
                                      "perto da praia", "a beira mar"]):
            return "Próximo ao mar"
        return ""

    def _extrair_coordenadas(self, html: str) -> tuple[str, str]:
        texto = html_lib.unescape(html or "")
        pares = [
            (
                r'"latitude"\s*:\s*"?(-?\d{1,3}\.\d+)"?',
                r'"longitude"\s*:\s*"?(-?\d{1,3}\.\d+)"?',
            ),
            (
                r'"lat"\s*:\s*"?(-?\d{1,3}\.\d+)"?',
                r'"lng"\s*:\s*"?(-?\d{1,3}\.\d+)"?',
            ),
            (
                r'latitude\s*=\s*["\'](-?\d{1,3}\.\d+)["\']',
                r'longitude\s*=\s*["\'](-?\d{1,3}\.\d+)["\']',
            ),
        ]
        for lat_pat, lng_pat in pares:
            lat = re.search(lat_pat, texto, re.IGNORECASE)
            lng = re.search(lng_pat, texto, re.IGNORECASE)
            if lat and lng:
                return lat.group(1), lng.group(1)

        # Fallback para arrays/pares no padrao [latitude, longitude].
        for lat, lng in re.findall(r"\[\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*\]", texto):
            try:
                lat_f = float(lat)
                lng_f = float(lng)
            except ValueError:
                continue
            if -35 <= lat_f <= 10 and -75 <= lng_f <= -30:
                return lat, lng
        return "", ""

    def _extrair_cep(self, html: str) -> str:
        texto = html or ""
        # 1ª prioridade: CEP em contexto de endereço (JSON-LD, schema, campos de cep)
        for pat in [
            r'"postalCode"\s*:\s*"(\d{5}-?\d{3})"',
            r'"cep"\s*:\s*"(\d{5}-?\d{3})"',
            r'postal[-_]?code["\s:=]+(\d{5}-?\d{3})',
            r'\bCEP\s*:?\s*(\d{5}[-\s]?\d{3})\b',
        ]:
            m = re.search(pat, texto, re.IGNORECASE)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if len(digits) == 8:
                    return f"{digits[:5]}-{digits[5:]}"
        # Fallback: primeiro CEP no texto (menos confiável)
        m = re.search(r"\b(\d{5}[-\s]?\d{3})\b", texto)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 8:
                return f"{digits[:5]}-{digits[5:]}"
        return ""

    def _resumo_tipologias(self, tipologias: list, html: str) -> str:
        if tipologias:
            linhas = ["Tipologias disponiveis:"]
            for t in tipologias:
                partes = []
                if t.get("area_m2"):
                    partes.append(f"{self._fmt_area(t['area_m2'])} m2")
                if t.get("quartos"):
                    partes.append(self._plural(t["quartos"], "quarto", "quartos"))
                if t.get("suites"):
                    partes.append(self._plural(t["suites"], "suíte", "suítes"))
                if t.get("banheiros"):
                    partes.append(self._plural(t["banheiros"], "banheiro", "banheiros"))
                if t.get("vagas"):
                    partes.append(self._plural(t["vagas"], "vaga", "vagas"))
                if t.get("preco"):
                    partes.append(f"a partir de {self._fmt_moeda(t['preco'])}")
                if partes:
                    linhas.append("- " + ", ".join(partes))
            return "\n".join(linhas)

        areas = re.findall(
            r'class=["\'][^"\']*list_value[^"\']*["\'][^>]*>'
            r"\s*([\d]+(?:\s*a\s*[\d]+)?)\s*</div>"
            r"\s*<div[^>]*>\s*m[²2]",
            html, re.IGNORECASE,
        )
        if not areas:
            areas = re.findall(r"(\d+(?:\s*a\s*\d+)?)\s*m[²2]", html, re.IGNORECASE)
        areas = list(dict.fromkeys(areas))
        if not areas:
            return ""
        linhas = ["Areas disponiveis:"] + [f"- {a} m2" for a in areas[:5]]
        return "\n".join(linhas)

    def _resumo_empreendimento(self, html: str, caracteristicas: list) -> str:
        estagio = self._extrair_estagio(html)
        estrutura = ", ".join(caracteristicas[:10])
        if estagio and estrutura:
            return f"O empreendimento esta em estagio {estagio.lower()} e conta com {estrutura}."
        if estagio:
            return f"O empreendimento esta em estagio {estagio.lower()}."
        if estrutura:
            return f"O empreendimento conta com {estrutura}."
        else:
            return ""

    def _extrair_caracteristicas(self, html: str) -> list:
        texto = self._normalizar_texto(self._texto_visivel(html))
        achadas = []

        def _tem_generico(termo: str, bloqueados: tuple[str, ...]) -> bool:
            limpo = texto
            for bloqueado in bloqueados:
                limpo = limpo.replace(bloqueado, " ")
            return bool(re.search(rf"(^| ){re.escape(termo)}($| )", limpo))

        # Piscina infantil deve ser verificada antes de piscina adulto
        # para não duplicar — marcamos o texto consumido via flag
        piscina_infantil_encontrada = False
        for nome, termos in _AMENIDADES:
            if self._normalizar_texto(nome) in _AMENIDADES_EXCLUIR:
                continue
            if nome == "Piscina adulto":
                # Só adiciona piscina adulto se "piscina" aparece além de "infantil"
                tem_piscina = any(self._normalizar_texto(t) in texto for t in termos)
                if tem_piscina and not piscina_infantil_encontrada:
                    achadas.append(nome)
                elif tem_piscina:
                    # Piscina infantil já foi marcada; adiciona adulto se tiver outro contexto
                    texto_sem_infantil = re.sub(r"piscina\s+infantil", "", texto)
                    if "piscina" in texto_sem_infantil:
                        achadas.append(nome)
                continue
            encontrou = any(self._normalizar_texto(t) in texto for t in termos)
            if encontrou:
                if nome == "Portaria" and not _tem_generico(
                    "portaria",
                    ("portaria 24 horas", "portaria 24h", "portaria 24", "portaria eletronica"),
                ):
                    continue
                if nome == "Segurança" and not _tem_generico(
                    "seguranca",
                    (
                        "circuito de seguranca",
                        "camera de seguranca",
                        "cameras de seguranca",
                        "seguranca 24 horas",
                        "seguranca 24h",
                        "seguranca 24",
                        "seguranca eletronica",
                        "sistema de alarme",
                    ),
                ):
                    continue
                if nome == "Piscina infantil":
                    piscina_infantil_encontrada = True
                achadas.append(nome)
        return filtrar_caracteristicas(achadas)

    def _normalizar_tipo_orulo(self, tipo: str, contexto: str = "") -> str:
        tipos = self._tipos_orulo(tipo, contexto)
        return tipos[0] if tipos else "Apartamento"

    def _tipos_orulo(self, tipo: str, contexto: str = "") -> list:
        tipos = normalizar_tipos_imovel(tipo, contexto)
        if tipos:
            return tipos
        return [tipo.strip() or "Apartamento"]

    def _extrair_tipologias(self, html: str, tipo_padrao: str) -> list:
        texto = self._texto_visivel(html)
        if "Tipologias" not in texto and "tipologias" not in texto:
            return []

        tipos_pos = []
        for tipo in _TIPOS:
            for m in re.finditer(rf"(?im)^\s*{re.escape(tipo)}\s*$", texto):
                tipos_pos.append((m.start(), tipo))
        tipos_pos.sort()

        def tipo_para(pos: int) -> str:
            escolhido = tipo_padrao
            for p, tipo in tipos_pos:
                if p <= pos:
                    escolhido = tipo
                else:
                    break
            return self._normalizar_tipo_orulo(escolhido or tipo_padrao, escolhido)

        def tipos_para(pos: int) -> list:
            escolhido = tipo_padrao
            for p, tipo in tipos_pos:
                if p <= pos:
                    escolhido = tipo
                else:
                    break
            return self._tipos_orulo(escolhido or tipo_padrao, escolhido)

        padrao_linha = re.compile(
            r"R\$\s*([\d.,]+)\s+"
            r"(\d+(?:[,.]\d+)?)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)",
            re.IGNORECASE,
        )

        tipologias = []
        vistos = set()
        for m in padrao_linha.finditer(texto):
            item = {
                "tipo_imovel": tipo_para(m.start()),
                "tipo_imovel_lista": tipos_para(m.start()),
                "preco": self._somente_digitos(m.group(1)),
                "area_m2": self._normalizar_area(m.group(2)),
                "quartos": self._normalizar_inteiro(m.group(3)),
                "suites": self._normalizar_inteiro(m.group(4)),
                # A tabela de tipologias da Órulo usa o ícone de banheira para suites.
                # Banheiros vêm do resumo superior do empreendimento.
                "banheiros": None,
                "vagas": self._normalizar_inteiro(m.group(5)),
            }
            chave = (
                item["tipo_imovel"], item["preco"], item["area_m2"],
                item["quartos"], item["suites"], item["vagas"],
            )
            if item["preco"] and chave not in vistos:
                vistos.add(chave)
                tipologias.append(item)

        return tipologias

    def _montar_dados_tipologia(self, base: dict, tipologia: dict, indice: int, total: int) -> dict:
        dados = dict(base)
        dados.pop("_dados_variacoes", None)
        dados.pop("_tipologias_resumo", None)
        dados.pop("_empreendimento_resumo", None)
        for campo in ["tipo_imovel", "tipo_imovel_lista", "preco", "area_m2", "quartos", "suites", "banheiros", "vagas"]:
            if tipologia.get(campo) is not None:
                dados[campo] = tipologia.get(campo, "")

        dados["titulo"] = self._titulo_tipologia(base, tipologia, total)
        dados["descricao_util"] = self._descricao_tipologia(base, tipologia, indice, total)
        return enriquecer_caracteristicas_por_texto(dados)

    def _titulo_tipologia(self, base: dict, tipologia: dict, total: int = 1) -> str:
        """Título da publicação: nome do empreendimento + quartos como diferenciador
        quando há mais de uma tipologia (ex: 'Águas do Atlântico III - 1 quarto')."""
        nome = base.get("titulo", "").strip()
        if not nome:
            return ""
        # Só acrescenta quartos quando há múltiplas tipologias para diferenciar
        if total > 1 and tipologia.get("quartos"):
            sufixo = self._plural(tipologia["quartos"], "quarto", "quartos")
            return f"{nome} - {sufixo}"
        return nome

    def _descricao_tipologia(self, base: dict, tipologia: dict, indice: int, total: int) -> str:
        tipo = tipologia.get("tipo_imovel") or base.get("tipo_imovel") or "Imovel"
        local = ", ".join(
            p for p in [
                base.get("bairro_extraido", "").strip(),
                base.get("cidade_extraida", "").strip(),
            ] if p
        )

        linhas = []
        if local:
            linhas.append(f"{tipo} em empreendimento localizado em {local}.")
        else:
            linhas.append(f"{tipo} em empreendimento com tipologias disponiveis para venda.")

        detalhes = []
        if tipologia.get("area_m2"):
            detalhes.append(f"{self._fmt_area(tipologia['area_m2'])} m2")
        if tipologia.get("quartos"):
            detalhes.append(self._plural(tipologia["quartos"], "quarto", "quartos"))
        if tipologia.get("suites"):
            detalhes.append(self._plural(tipologia["suites"], "suíte", "suítes"))
        if tipologia.get("banheiros"):
            detalhes.append(self._plural(tipologia["banheiros"], "banheiro", "banheiros"))
        if tipologia.get("vagas"):
            detalhes.append(self._plural(tipologia["vagas"], "vaga de garagem", "vagas de garagem"))
        if detalhes:
            linhas.append("Esta opcao conta com " + ", ".join(detalhes) + ".")

        if tipologia.get("preco"):
            linhas.append(f"Valor a partir de {self._fmt_moeda(tipologia['preco'])}.")

        # Bloco "Outras informações" com estágio e dados estruturais
        info_add = base.get("_info_adicional") or {}
        campos_info = []
        if base.get("estagio_imovel"):
            campos_info.append(f"Estagio: {base['estagio_imovel']}")
        if info_add.get("estoque"):
            campos_info.append(f"Estoque: {info_add['estoque']}")
        if info_add.get("lancamento"):
            campos_info.append(f"Lancamento: {info_add['lancamento']}")
        if info_add.get("unidades_por_andar"):
            campos_info.append(f"Unidades por andar: {info_add['unidades_por_andar']}")
        if info_add.get("entrega"):
            campos_info.append(f"Entrega: {info_add['entrega']}")
        if info_add.get("total_unidades"):
            campos_info.append(f"Total de unidades: {info_add['total_unidades']}")
        if info_add.get("andares"):
            campos_info.append(f"Numero de andares: {info_add['andares']}")
        if campos_info:
            linhas.append("Outras informacoes: " + " - ".join(campos_info))

        empreendimento = base.get("_empreendimento_resumo", "").strip()
        if empreendimento:
            linhas.append(empreendimento)

        tipologias_resumo = base.get("_tipologias_resumo", "").strip()
        if tipologias_resumo:
            linhas.append(tipologias_resumo)

        if total > 1:
            linhas.append(
                f"Tipologia {indice} de {total} disponivel neste empreendimento. "
                "As fotos podem representar o empreendimento, areas comuns ou unidade decorada."
            )

        linhas.append("Consulte disponibilidade, condicoes comerciais e detalhes atualizados.")
        return "\n\n".join(linhas)

    def _texto_visivel(self, html: str) -> str:
        texto = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "\n", html)
        texto = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", texto)
        texto = re.sub(r"<[^>]+>", " ", texto)
        texto = html_lib.unescape(texto)
        texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
        texto = re.sub(r"\n\s+", "\n", texto)
        return texto

    def _normalizar_texto(self, texto: str) -> str:
        texto = html_lib.unescape(texto or "").lower()
        mapa = str.maketrans("áàâãéêíóôõúüç", "aaaaeeiooouuc")
        texto = texto.translate(mapa)
        return re.sub(r"\s+", " ", texto).strip()

    def _somente_digitos(self, valor: str) -> str:
        return re.sub(r"[^\d]", "", valor or "")

    def _normalizar_area(self, valor: str) -> str:
        nums = re.findall(r"\d+", valor or "")
        return nums[0] if nums else ""

    def _normalizar_inteiro(self, valor: str) -> str:
        nums = re.findall(r"\d+", valor or "")
        if not nums:
            return ""
        n = int(nums[0])
        return str(n) if n > 0 else ""

    def _fmt_area(self, valor: str) -> str:
        return str(valor or "").strip().replace(".", ",")

    def _fmt_moeda(self, valor: str) -> str:
        digitos = self._somente_digitos(valor)
        if not digitos:
            return ""
        partes = []
        while digitos:
            partes.append(digitos[-3:])
            digitos = digitos[:-3]
        return "R$ " + ".".join(reversed(partes))

    def _plural(self, valor: str, singular: str, plural: str) -> str:
        try:
            n = int(str(valor).strip())
        except Exception:
            n = 0
        return f"{n} {singular if n == 1 else plural}" if n else ""

    def _deduplicar_urls_imagem(self, urls: list) -> list:
        saida = []
        vistos = set()
        for url in urls:
            if not url:
                continue
            limpa = html_lib.unescape(str(url)).replace("\\/", "/").split("?")[0]
            chave = limpa.lower()
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append(limpa)
        return saida

    def _filtrar_urls_imagens(self, urls: list) -> list:
        filtradas = []
        for url in urls or []:
            limpa = html_lib.unescape(str(url)).replace("\\/", "/").split("?")[0]
            if not re.search(r"https://static\.orulo\.com\.br/images/.+\.(?:jpeg|jpg|png|webp)$", limpa, re.IGNORECASE):
                continue
            if self._imagem_eh_planta(limpa):
                continue
            filtradas.append(limpa)
        return self._deduplicar_urls_imagem(filtradas)[:_MAX_IMAGENS]

    def _imagem_eh_planta(self, url: str, contexto: str = "") -> bool:
        texto = self._normalizar_texto(f"{url} {contexto}")
        return any(self._normalizar_texto(t) in texto for t in _TERMOS_PLANTA)

    def _pontuar_imagem(self, url: str, contexto: str, ordem: int) -> tuple:
        texto = self._normalizar_texto(f"{url} {contexto}")
        score = 0
        if any(self._normalizar_texto(t) in texto for t in _TERMOS_GALERIA):
            score += 20
        if any(self._normalizar_texto(t) in texto for t in ["fachada", "facade", "building", "empreendimento", "cover"]):
            score += 10
        if any(self._normalizar_texto(t) in texto for t in ["thumb", "thumbnail", "small"]):
            score -= 2
        return (-score, ordem)

    def _contexto_tag_imagem(self, html: str, inicio: int, fim: int) -> str:
        tag_ini = html.rfind("<", 0, inicio)
        tag_fim = html.find(">", fim)
        if tag_ini < 0:
            tag_ini = max(0, inicio - 60)
        if tag_fim < 0:
            tag_fim = min(len(html), fim + 60)
        anterior = html[max(0, tag_ini - 70):tag_ini]
        return anterior + html[tag_ini:tag_fim + 1]

    def _inicio_secao_plantas(self, html: str) -> int:
        marcadores = [
            r"<h[1-6][^>]*>\s*Plantas\b",
            r">\s*Plantas\s*<",
            r">\s*Mais plantas\s*<",
            r"\bfloor[_-]?plan",
            r"class=[\"'][^\"']*(?:plant|floor)",
        ]
        posicoes = []
        for pat in marcadores:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                posicoes.append(m.start())
        return min(posicoes) if posicoes else -1


    def _imagem_hash_visual(self, conteudo: bytes) -> str:
        if Image is None:
            return ""
        try:
            img = Image.open(BytesIO(conteudo)).convert("L").resize((8, 8))
            pixels = list(img.getdata())
            media = sum(pixels) / len(pixels)
            return "".join("1" if p >= media else "0" for p in pixels)
        except Exception:
            return ""

    def _hash_parecido(self, a: str, b: str) -> bool:
        if not a or not b or len(a) != len(b):
            return False
        distancia = sum(1 for x, y in zip(a, b) if x != y)
        return distancia <= 3

    def _extrair_imagens(self, html: str) -> list:
        html_img = html_lib.unescape(html or "").replace("\\/", "/")
        inicio_plantas = self._inicio_secao_plantas(html_img)
        padrao = re.compile(
            r"https://static\.orulo\.com\.br/images/[^\"'\s)]+?\.(?:jpeg|jpg|png|webp)(?:\?\d+)?",
            re.IGNORECASE,
        )

        def _coletar(html_src: str, apenas_antes: int = -1) -> list:
            cands = []
            for ordem, m in enumerate(padrao.finditer(html_src)):
                if apenas_antes >= 0 and m.start() >= apenas_antes:
                    continue
                u = m.group(0)
                base = u.split("?")[0]
                contexto_planta = self._contexto_tag_imagem(html_src, m.start(), m.end())
                if self._imagem_eh_planta(base, contexto_planta):
                    continue
                contexto = html_src[max(0, m.start() - 350):m.end() + 350]
                cands.append((self._pontuar_imagem(base, contexto, ordem), base))
            cands.sort(key=lambda item: item[0])
            return self._deduplicar_urls_imagem([u for _, u in cands])

        # 1ª passagem: apenas galeria principal (antes da seção de plantas)
        urls = _coletar(html_img, apenas_antes=inicio_plantas)

        # 2ª passagem: se ainda faltar imagens, coleta do resto da página
        # (seções de tipologias, ambientes, etc.) filtrando plantas individualmente
        if len(urls) < _MAX_IMAGENS:
            extras = _coletar(html_img, apenas_antes=-1)  # sem restrição de posição
            faltam = _MAX_IMAGENS - len(urls)
            novas = [u for u in extras if u not in set(urls)][:faltam]
            if novas:
                logger.info(f"Orulo: adicionando {len(novas)} imagem(ns) de outras secoes")
            urls = self._deduplicar_urls_imagem(urls + novas)

        if not urls:
            og = self._meta(html, "og:image")
            if og and not self._imagem_eh_planta(og):
                urls.append(og.split("?")[0])

        return self._deduplicar_urls_imagem(urls)[:_MAX_IMAGENS]
