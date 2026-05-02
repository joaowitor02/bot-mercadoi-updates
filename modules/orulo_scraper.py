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
from io import BytesIO
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from modules.logger import Logger

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
    "Apartamento Cobertura", "Apartamento Duplex", "Apto. Cobertura", "Apto. Duplex",
    "Cobertura Duplex", "Duplex Cobertura", "Cobertura", "Duplex",
    "Apartamento", "Casa", "Terreno", "Studio", "Flat",
    "Sala Comercial", "Loja", "Galpao", "Lote",
]

_AMENIDADES = [
    ("Academia", ["academia", "fitness"]),
    ("Piscina", ["piscina"]),
    ("Salao de festas", ["salao de festas", "salão de festas"]),
    ("Espaco gourmet", ["espaco gourmet", "espaço gourmet", "gourmet"]),
    ("Churrasqueira", ["churrasqueira"]),
    ("Playground", ["playground"]),
    ("Brinquedoteca", ["brinquedoteca"]),
    ("Coworking", ["coworking"]),
    ("Pet place", ["pet place", "pet care"]),
    ("Lavanderia", ["lavanderia"]),
    ("Bicicletario", ["bicicletario", "bicicletário"]),
    ("Portaria", ["portaria"]),
    ("Elevador", ["elevador"]),
    ("Quadra", ["quadra"]),
    ("Rooftop", ["rooftop"]),
    ("Sauna", ["sauna"]),
    ("Solarium", ["solarium"]),
    ("Deck", ["deck"]),
]

_TERMOS_PLANTA = [
    "planta", "plantas", "floorplan", "floor plan", "floor_plan",
    "blueprint", "unit plan", "unit_plan", "implantacao", "implantaÃ§Ã£o",
    "layout", "typology", "tipologia",
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

        dados_variacoes = dados.pop("_dados_variacoes", [])

        logger.info(
            f"Orulo: '{dados['titulo'][:70]}' | preco={dados['preco']} | "
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
        tipos_imovel_lista = self._tipos_orulo(tipo_imovel, og_title)
        tipo_imovel = tipos_imovel_lista[0] if tipos_imovel_lista else "Apartamento"

        nome_emp = self._nome_empreendimento(html)
        if nome_emp:
            partes = [p for p in [tipo_imovel, bairro, cidade] if p]
            titulo = nome_emp + (" - " + ", ".join(partes) if partes else "")
        else:
            titulo = og_title or self._titulo_texto(html)

        tipologias = self._extrair_tipologias(html, tipo_imovel)
        tipologia_base = tipologias[0] if tipologias else {}

        preco = tipologia_base.get("preco") or self._extrair_preco(html)
        area = tipologia_base.get("area_m2") or self._extrair_num_range(html, r"m[²2]")
        quartos = tipologia_base.get("quartos") or self._extrair_num_range(html, r"quarto")
        suites = tipologia_base.get("suites") or self._extrair_num_range(html, r"su[ií]te")
        banheiros = tipologia_base.get("banheiros") or self._extrair_num_range(html, r"banheiro")
        vagas = tipologia_base.get("vagas") or self._extrair_num_range(html, r"vaga")

        resumo = self._resumo_tipologias(tipologias, html)
        caracteristicas = self._extrair_caracteristicas(html)
        resumo_empreendimento = self._resumo_empreendimento(html, caracteristicas)
        descricao = "\n\n".join(p for p in [og_desc, resumo_empreendimento, resumo] if p).strip()

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
            "caracteristicas": caracteristicas,
            "_tipologias_resumo": resumo,
            "_empreendimento_resumo": resumo_empreendimento,
        }
        if tipologias:
            dados["_dados_variacoes"] = [
                self._montar_dados_tipologia(dados, t, i + 1, len(tipologias))
                for i, t in enumerate(tipologias)
            ]
        return dados

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

    def _resumo_tipologias(self, tipologias: list, html: str) -> str:
        if tipologias:
            linhas = ["Tipologias disponiveis:"]
            for t in tipologias:
                partes = []
                if t.get("area_m2"):
                    partes.append(f"{self._fmt_area(t['area_m2'])} m2")
                if t.get("quartos"):
                    partes.append(self._plural(t["quartos"], "quarto", "quartos"))
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
        for nome, termos in _AMENIDADES:
            if any(self._normalizar_texto(t) in texto for t in termos):
                achadas.append(nome)
        return achadas

    def _normalizar_tipo_orulo(self, tipo: str, contexto: str = "") -> str:
        tipos = self._tipos_orulo(tipo, contexto)
        return tipos[0] if tipos else "Apartamento"

    def _tipos_orulo(self, tipo: str, contexto: str = "") -> list:
        texto = self._normalizar_texto(f"{tipo} {contexto}")
        tipos = []
        if "cobertura" in texto:
            tipos.append("Apto. Cobertura")
        if "duplex" in texto:
            tipos.append("Apto. Duplex")
        if tipos:
            return tipos
        if "flat" in texto:
            return ["Apto. Flat"]
        if "garden" in texto:
            return ["Apto. Garden"]
        if "studio" in texto or "apart" in texto or "apto" in texto:
            return ["Apartamento"]
        if "casa" in texto:
            return ["Casa"]
        if "terreno" in texto or "lote" in texto:
            return ["Terreno"]
        if "sala" in texto or "comercial" in texto or "loja" in texto:
            return ["Sala Comercial"]
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
                "suites": "",
                "banheiros": self._normalizar_inteiro(m.group(4)),
                "vagas": self._normalizar_inteiro(m.group(5)),
            }
            chave = (
                item["tipo_imovel"], item["preco"], item["area_m2"],
                item["quartos"], item["banheiros"], item["vagas"],
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

        dados["titulo"] = self._titulo_tipologia(base, tipologia)
        dados["descricao_util"] = self._descricao_tipologia(base, tipologia, indice, total)
        return dados

    def _titulo_tipologia(self, base: dict, tipologia: dict) -> str:
        nome = base.get("titulo", "").strip()
        partes = []
        if tipologia.get("area_m2"):
            partes.append(f"{self._fmt_area(tipologia['area_m2'])}m2")
        if tipologia.get("quartos"):
            partes.append(self._plural(tipologia["quartos"], "quarto", "quartos"))
        resumo = ", ".join(partes)
        return f"{nome} - {resumo}" if nome and resumo else nome

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
        if tipologia.get("banheiros"):
            detalhes.append(self._plural(tipologia["banheiros"], "banheiro", "banheiros"))
        if tipologia.get("vagas"):
            detalhes.append(self._plural(tipologia["vagas"], "vaga de garagem", "vagas de garagem"))
        if detalhes:
            linhas.append("Esta opcao conta com " + ", ".join(detalhes) + ".")

        if tipologia.get("preco"):
            linhas.append(f"Valor a partir de {self._fmt_moeda(tipologia['preco'])}.")

        if base.get("estagio_imovel"):
            linhas.append(f"Estagio do empreendimento: {base['estagio_imovel']}.")

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
        padrao = re.compile(
            r"https://static\.orulo\.com\.br/images/[^\"'\s)]+?\.(?:jpeg|jpg|png|webp)(?:\?\d+)?",
            re.IGNORECASE,
        )
        candidatos = []
        for ordem, m in enumerate(padrao.finditer(html_img)):
            u = m.group(0)
            base = u.split("?")[0]
            contexto = html_img[max(0, m.start() - 350):m.end() + 350]
            contexto_planta = self._contexto_tag_imagem(html_img, m.start(), m.end())
            if self._imagem_eh_planta(base, contexto_planta):
                continue
            candidatos.append((self._pontuar_imagem(base, contexto, ordem), base))

        candidatos.sort(key=lambda item: item[0])
        urls = self._deduplicar_urls_imagem([u for _, u in candidatos])

        if not urls:
            og = self._meta(html, "og:image")
            if og and not self._imagem_eh_planta(og):
                urls.append(og.split("?")[0])

        return self._deduplicar_urls_imagem(urls)[:_MAX_IMAGENS]
