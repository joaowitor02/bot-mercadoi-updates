"""
Scraper HTTP para anúncios de imóveis do OLX Brasil.
Extrai dados via __NEXT_DATA__ (Next.js) sem browser.
"""

import json
import re
import os
import asyncio
import httpx
from modules.logger import Logger

logger = Logger("olx_scraper")

_OLX_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?olx\.com\.br/",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

_TIPO_MAP = {
    "apartamento": "Apartamento",
    "apartamentos": "Apartamento",
    "casa":         "Casa",
    "casas":        "Casa",
    "terreno":      "Terreno",
    "terrenos":     "Terreno",
    "lote":         "Terreno",
    "sala":         "Sala Comercial",
    "comercial":    "Sala Comercial",
    "loja":         "Sala Comercial",
    "galpao":       "Sala Comercial",
    "galpão":       "Sala Comercial",
    "flat":         "Apto. Flat",
    "cobertura":    "Apto. Cobertura",
    "studio":       "Apartamento",
    "kitnet":       "Apartamento",
    "sitio":        "Sítio",
    "sítio":        "Sítio",
    "chacara":      "Chácara",
    "chácara":      "Chácara",
    "fazenda":      "Fazenda",
}


def url_valida(url: str) -> bool:
    return bool(_OLX_URL_RE.match(url.strip()))


def _normalizar_tipo(texto: str) -> str:
    t = texto.lower().strip()
    for chave, valor in _TIPO_MAP.items():
        if chave in t:
            return valor
    return "Apartamento"


def _limpar_preco(valor) -> str:
    if not valor:
        return ""
    s = str(valor)
    apenas_digitos = re.sub(r"\D", "", s)
    # Remove zeros à esquerda absurdos (ex: centavos vieram junto)
    if len(apenas_digitos) > 10:
        apenas_digitos = apenas_digitos[:10]
    return apenas_digitos or ""


def _prop(properties: list, *nomes: str) -> str:
    """Extrai valor de uma propriedade da lista de properties do OLX."""
    for item in properties:
        nome = (item.get("name") or item.get("label") or "").lower()
        for n in nomes:
            if n in nome:
                val = item.get("value") or item.get("values") or ""
                if isinstance(val, list):
                    val = val[0] if val else ""
                return str(val).strip()
    return ""


def _extrair_next_data(html: str) -> dict | None:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _parse_ad(data: dict) -> dict | None:
    """Navega pela estrutura do __NEXT_DATA__ e retorna o objeto do anúncio."""
    pp = data.get("props", {}).get("pageProps", {})

    # Tenta vários caminhos conhecidos
    for caminho in [
        lambda d: d.get("ad"),
        lambda d: d.get("adData", {}).get("ad"),
        lambda d: d.get("initialProps", {}).get("ad"),
        lambda d: d.get("data", {}).get("ad"),
        lambda d: d.get("listing"),
    ]:
        try:
            ad = caminho(pp)
            if ad and isinstance(ad, dict):
                return ad
        except Exception:
            continue
    return None


def _montar_dados(ad: dict, url: str) -> dict:
    props = ad.get("properties") or ad.get("params") or []

    titulo   = ad.get("subject") or ad.get("title") or ""
    descricao = ad.get("body") or ad.get("description") or ""

    # Preço
    preco_raw = (
        ad.get("price", {}) if isinstance(ad.get("price"), dict) else {}
    )
    preco_val = (
        preco_raw.get("value")
        or preco_raw.get("price")
        or ad.get("priceValue")
        or ad.get("price")
        or ""
    )
    preco = _limpar_preco(preco_val)

    # Operação
    tipo_negocio = (
        ad.get("type", {}).get("label", "") if isinstance(ad.get("type"), dict) else ""
    ) or ad.get("adType") or ad.get("businessType") or ""
    operacao = "Em Aluguel" if "aluguel" in tipo_negocio.lower() or "rent" in tipo_negocio.lower() else "A Venda"

    # Tipo de imóvel
    cat = (
        ad.get("category", {}).get("name", "") if isinstance(ad.get("category"), dict) else ""
    ) or ad.get("categoryName") or ""
    tipo_imovel = _normalizar_tipo(cat or titulo)

    # Localização
    loc = ad.get("location") or ad.get("address") or {}
    cidade = (
        loc.get("municipality") or loc.get("city") or
        loc.get("municipalityLabel") or loc.get("municipalityCode") or ""
    )
    bairro = (
        loc.get("neighbourhood") or loc.get("district") or
        loc.get("neighbourhoodLabel") or loc.get("zone") or ""
    )
    endereco = loc.get("address") or loc.get("street") or ""

    # Características numéricas
    quartos  = _prop(props, "room", "quarto", "bedroom", "dormit")
    suites   = _prop(props, "suite")
    banheiros = _prop(props, "bathroom", "banheiro")
    vagas    = _prop(props, "garage", "vaga", "parking")
    area     = _prop(props, "square_meter", "area", "m2", "tamanho")

    # Corrige suites > quartos
    try:
        if int(suites) > int(quartos):
            suites = str(int(quartos) // 2) if int(quartos) > 1 else ""
    except Exception:
        pass

    # Imagens
    imgs_raw = ad.get("images") or ad.get("photos") or []
    imagens_urls = []
    for img in imgs_raw:
        if isinstance(img, dict):
            u = img.get("original") or img.get("url") or img.get("src") or ""
        elif isinstance(img, str):
            u = img
        else:
            continue
        if u:
            imagens_urls.append(u)

    # Telefone → WhatsApp (se disponível)
    phone = ad.get("phone") or (ad.get("user") or {}).get("phone") or ""
    wa = ""
    if phone:
        digits = re.sub(r"\D", "", str(phone))
        if digits:
            wa = f"https://wa.me/55{digits}"

    dados = {
        "titulo":          titulo,
        "descricao_util":  descricao,
        "preco":           preco,
        "tipo_imovel":     tipo_imovel,
        "operacao":        operacao,
        "quartos":         quartos,
        "suites":          suites,
        "banheiros":       banheiros,
        "vagas":           vagas,
        "area_m2":         area,
        "cidade_extraida": cidade,
        "bairro_extraido": bairro,
        "endereco":        endereco,
        "url_publicacao":  url,
        "whatsapp_url":    wa,
        "instagram_url":   "",
        "estagio_imovel":  "",
        "andar":           "",
        "elevador":        "",
        "area_terreno":    "",
        "ano_construcao":  "",
        "condominio":      "",
        "caracteristicas": [],
    }
    return dados, imagens_urls


class OlxScraper:
    async def extrair(self, url: str) -> dict:
        """
        Extrai dados de um anúncio OLX.
        Tenta httpx primeiro (rápido); se bloqueado pelo Cloudflare, usa Playwright headless.
        Retorna:
          ok=True  → {ok, dados, imagens_urls}
          ok=False → {ok, motivo}
        """
        url = url.strip()
        if not url_valida(url):
            return {"ok": False, "motivo": "url_invalida"}

        # Tentativa 1: httpx (sem browser)
        html = await self._fetch_httpx(url)
        if html == "404":
            return {"ok": False, "motivo": "nao_encontrado"}

        # Tentativa 2: Playwright headless (contorna Cloudflare)
        if html in (None, "403"):
            logger.info("OLX bloqueou HTTP — tentando Playwright headless...")
            html = await self._fetch_playwright(url)

        if not html:
            return {"ok": False, "motivo": "acesso_restrito"}

        next_data = _extrair_next_data(html)
        if not next_data:
            logger.warning(f"__NEXT_DATA__ não encontrado em {url}")
            return {"ok": False, "motivo": "estrutura_desconhecida"}

        ad = _parse_ad(next_data)
        if not ad:
            logger.warning(f"Objeto de anúncio não encontrado no __NEXT_DATA__ de {url}")
            return {"ok": False, "motivo": "estrutura_desconhecida"}

        dados, imagens_urls = _montar_dados(ad, url)
        if not dados.get("titulo"):
            return {"ok": False, "motivo": "dados_insuficientes"}

        logger.info(
            f"OLX extraído: '{dados['titulo'][:60]}' | "
            f"{dados['cidade_extraida']} | R${dados['preco']} | "
            f"{len(imagens_urls)} imagem(ns)"
        )
        return {"ok": True, "dados": dados, "imagens_urls": imagens_urls}

    async def _fetch_httpx(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers=_HEADERS,
            ) as client:
                r = await client.get(url)
            if r.status_code == 404:
                return "404"
            if r.status_code in (403, 429):
                return "403"
            if r.status_code != 200:
                return None
            return r.text
        except Exception as e:
            logger.warning(f"OLX httpx falhou: {e}")
            return None

    async def _fetch_playwright(self, url: str) -> str | None:
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
                context = await browser.new_context(
                    user_agent=_HEADERS["User-Agent"],
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = await context.new_page()
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # Aguarda __NEXT_DATA__ estar disponível
                try:
                    await page.wait_for_function(
                        "document.getElementById('__NEXT_DATA__') !== null",
                        timeout=10000,
                    )
                except Exception:
                    pass
                html = await page.content()
                await browser.close()
                return html
        except Exception as e:
            logger.warning(f"OLX Playwright falhou: {e}")
            return None

    async def baixar_imagens(self, imagens_urls: list, downloads_path: str) -> list[str]:
        """Baixa imagens do OLX diretamente (sem Apify)."""
        os.makedirs(downloads_path, exist_ok=True)
        arquivos = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for i, url_img in enumerate(imagens_urls[:10]):  # máx 10 imagens
                try:
                    r = await client.get(url_img, headers={"Referer": "https://www.olx.com.br/"})
                    if r.status_code != 200:
                        continue
                    ext = ".jpg"
                    ct = r.headers.get("content-type", "")
                    if "png" in ct:
                        ext = ".png"
                    caminho = os.path.join(downloads_path, f"olx_{i+1}{ext}")
                    with open(caminho, "wb") as f:
                        f.write(r.content)
                    arquivos.append(caminho)
                    logger.info(f"Imagem OLX {i+1}/{len(imagens_urls)} baixada")
                except Exception as e:
                    logger.warning(f"Erro ao baixar imagem OLX {i+1}: {e}")
        return arquivos
