"""
Extrai legenda de posts do Instagram usando o Chrome já aberto (porta 9222).
O Chrome precisa estar logado no Instagram para acessar posts sem bloqueio.
"""

import re
import sys
from playwright.async_api import async_playwright
from modules.logger import Logger

logger = Logger("instagram_chrome_scraper")

_INSTAGRAM_URL_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


async def extrair_via_chrome(url: str) -> dict:
    """
    Abre o link do Instagram em uma nova aba do Chrome já em execução (porta 9222)
    e extrai a legenda do post.

    Retorna: {caption, url_publicacao, perfil_instagram, ok, motivo}
    """
    if not _INSTAGRAM_URL_RE.search(url):
        return _falha(url, "url_invalida")

    if sys.platform != "win32":
        return _falha(url, "erro_rede")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            try:
                logger.info(f"Abrindo Instagram no Chrome: {url}")
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")

                # Aguarda conteúdo carregar
                await page.wait_for_timeout(3000)

                # Verifica se foi redirecionado para login
                current = page.url
                if "accounts/login" in current or "login" in current:
                    logger.warning("Chrome não está logado no Instagram")
                    return _falha(url, "acesso_restrito")

                # Extrai legenda via múltiplas estratégias
                caption = await _extrair_caption(page)
                perfil = await _extrair_perfil(page, url)

                if caption:
                    logger.info(f"Legenda extraída via Chrome ({len(caption)} chars): {caption[:100]}")
                    return {
                        "caption": caption,
                        "url_publicacao": url,
                        "perfil_instagram": perfil,
                        "ok": True,
                        "motivo": "ok",
                    }

                logger.warning("Legenda vazia — post privado ou sem texto")
                return _falha(url, "post_privado")

            finally:
                await page.close()

    except Exception as e:
        logger.error(f"Erro ao extrair Instagram via Chrome: {e}")
        return _falha(url, "erro_rede")


async def _extrair_caption(page) -> str:
    estrategias = [
        # Seletor específico do Instagram para a legenda do post
        'article h1',
        'div[data-testid="post-comment-root"] span',
        'article div > span > span',
        'div._a9zs span',
        # Meta tag og:description como fallback
    ]
    for sel in estrategias:
        try:
            elem = await page.query_selector(sel)
            if elem:
                texto = (await elem.inner_text()).strip()
                if texto and len(texto) > 10:
                    return texto
        except Exception:
            pass

    # Fallback: meta tag og:description
    try:
        content = await page.evaluate(
            "() => { const m = document.querySelector('meta[property=\"og:description\"]'); "
            "return m ? m.getAttribute('content') : ''; }"
        )
        if content and len(content) > 10:
            # Remove o prefixo "N Likes, N Comments - @user on Instagram: "
            m = re.search(r'on Instagram:\s*[“"]?(.*)', content, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip().strip('"').strip('”') if m else content.strip()
    except Exception:
        pass

    return ""


async def _extrair_perfil(page, url: str) -> str:
    try:
        # Tenta pegar o @usuario do link da página
        href = await page.evaluate(
            "() => { const a = document.querySelector('header a[href*=\"/\"]'); "
            "return a ? a.getAttribute('href') : ''; }"
        )
        if href:
            usuario = href.strip("/").split("/")[-1]
            if usuario:
                return f"https://www.instagram.com/{usuario}/"
    except Exception:
        pass

    # Fallback: extrai do padrão da URL do post
    m = re.search(r'instagram\.com/([^/p?#][^/?#]*)/(?:p|reel|tv)/', url)
    if m:
        return f"https://www.instagram.com/{m.group(1)}/"
    return ""


def _falha(url: str, motivo: str) -> dict:
    return {
        "caption": "",
        "url_publicacao": url,
        "perfil_instagram": "",
        "ok": False,
        "motivo": motivo,
    }
