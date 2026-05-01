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
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from modules.logger import Logger

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

_TIPOS = [
    "Apartamento", "Casa", "Terreno", "Studio", "Flat",
    "Cobertura", "Sala Comercial", "Loja", "Galpao", "Lote",
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
    def __init__(self, email: str = "", senha: str = "", profile_path: str = ""):
        self.email = (email or "").strip()
        self.senha = (senha or "").strip()
        self.profile_path = (
            profile_path
            or os.path.join(tempfile.gettempdir(), "orulo_browser_profile")
        )

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

        if not dados.get("titulo") and self.email and self.senha:
            logger.info("Orulo: tentando leitura autenticada")
            auth = await self._fetch_autenticado(url)
            if not auth.get("ok"):
                return auth
            html = auth["html"]
            dados = self._extrair_dados(html, url)
            imagens_urls = self._extrair_imagens(html)

        if not dados.get("titulo"):
            return {"ok": False, "motivo": "estrutura_desconhecida"}

        logger.info(
            f"Orulo: '{dados['titulo'][:70]}' | preco={dados['preco']} | "
            f"{len(imagens_urls)} foto(s)"
        )
        return {"ok": True, "dados": dados, "imagens_urls": imagens_urls}

    async def baixar_imagens(self, urls: list, downloads_path: str) -> list:
        """Baixa ate 12 imagens e retorna lista de caminhos locais."""
        os.makedirs(downloads_path, exist_ok=True)
        caminhos = []
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers=_HEADERS
        ) as client:
            for i, img_url in enumerate(urls[:12]):
                try:
                    r = await client.get(img_url)
                    if r.status_code == 200:
                        ext = img_url.split(".")[-1].split("?")[0].lower()
                        ext = ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"
                        nome = f"orulo_{i + 1:02d}.{ext}"
                        caminho = os.path.join(downloads_path, nome)
                        with open(caminho, "wb") as fh:
                            fh.write(r.content)
                        caminhos.append(caminho)
                        logger.info(f"Orulo: baixado {nome} ({len(r.content) // 1024} KB)")
                except Exception as e:
                    logger.warning(f"Orulo: falha ao baixar imagem {img_url[:60]}: {e}")
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

        nome_emp = self._nome_empreendimento(html)
        if nome_emp:
            partes = [p for p in [tipo_imovel, bairro, cidade] if p]
            titulo = nome_emp + (" - " + ", ".join(partes) if partes else "")
        else:
            titulo = og_title or self._titulo_texto(html)

        preco = self._extrair_preco(html)
        area = self._extrair_num_range(html, r"m[²2]")
        quartos = self._extrair_num_range(html, r"quarto")
        suites = self._extrair_num_range(html, r"su[ií]te")
        banheiros = self._extrair_num_range(html, r"banheiro")
        vagas = self._extrair_num_range(html, r"vaga")

        resumo = self._resumo_tipologias(html)
        descricao = "\n\n".join(p for p in [og_desc, resumo] if p).strip()

        return {
            "titulo": titulo,
            "descricao_util": descricao,
            "tipo_imovel": tipo_imovel,
            "operacao": "A Venda",
            "preco": preco,
            "quartos": quartos,
            "suites": suites,
            "banheiros": banheiros,
            "vagas": vagas,
            "area_m2": area,
            "area_terreno": "",
            "ano_construcao": "",
            "condominio": "",
            "andar": "",
            "elevador": "",
            "estagio_imovel": self._extrair_estagio(html),
            "endereco": endereco,
            "bairro_extraido": bairro,
            "cidade_extraida": cidade,
            "url_publicacao": url,
            "whatsapp_url": "",
            "instagram_url": "",
            "caracteristicas": [],
        }

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
        if "." in text:
            partes = text.split(".", 1)
            endereco = partes[0].strip(" -")
            bairro = re.split(r"\s*[-–]\s*", partes[1].strip())[0].strip()
        else:
            partes = re.split(r"\s*[-–]\s*", text, maxsplit=1)
            endereco = partes[0].strip()
            bairro = partes[1].strip() if len(partes) > 1 else ""

        return tipo, endereco, bairro, cidade

    def _nome_empreendimento(self, html: str) -> str:
        m = re.search(r"<title>[^|<]+\|\s*([^<]+)</title>", html, re.IGNORECASE)
        if m:
            nome = m.group(1).strip()
            nome = re.split(r"\s*[-–]\s*[A-ZÀ-Ú]", nome)[0].strip()
            if nome and "Login" not in nome:
                return nome
        m = re.search(r'building_name\s*=\s*["\']([^"\']+)["\']', html)
        if m:
            return m.group(1).strip()
        return ""

    def _titulo_texto(self, html: str) -> str:
        for pat in [
            r"(?:Empreendimento|Condominio|Condomínio)\s+([A-Z][^\n\r]{5,90})",
            r"<h1[^>]*>\s*([^<]{5,120})\s*</h1>",
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip()
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

    def _resumo_tipologias(self, html: str) -> str:
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

    def _extrair_imagens(self, html: str) -> list:
        encontradas = re.findall(
            r"https://static\.orulo\.com\.br/images/[^\"'\s)]+?\.(?:jpeg|jpg|png|webp)(?:\?\d+)?",
            html,
        )
        urls = []
        for u in encontradas:
            base = u.split("?")[0]
            if base not in urls:
                urls.append(base)

        if not urls:
            og = self._meta(html, "og:image")
            if og:
                urls.append(og.split("?")[0])

        return urls[:12]
