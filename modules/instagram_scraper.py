"""
Extrai dados de posts públicos do Instagram via meta tags OG.
Usa o user-agent do Facebook crawler, que o Instagram sempre serve sem bloqueio.
"""

import re
import httpx
from modules.logger import Logger

logger = Logger("instagram_scraper")

_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'",
    "&#x27;": "'", "&#x2F;": "/", "&#x2f;": "/",
}


_INSTAGRAM_URL_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)

_LOGIN_INDICATORS = ("log in", "login", "sign in", "loginpage", "accounts/login")


class InstagramScraper:
    async def extrair(self, url: str) -> dict:
        """
        Extrai caption e metadados de um post público do Instagram.
        Retorna: {caption, url_publicacao, perfil_instagram, tipo_midia_hint, ok, motivo}
        motivo: 'ok' | 'url_invalida' | 'nao_encontrado' | 'acesso_restrito' | 'post_privado' | 'erro_rede'
        """
        logger.info(f"Extraindo dados do Instagram: {url}")

        if not _INSTAGRAM_URL_RE.search(url):
            logger.warning(f"URL não parece ser um post do Instagram: {url}")
            return _falha(url, "url_invalida")

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=20, follow_redirects=True
            ) as client:
                resp = await client.get(url)

            if resp.status_code == 404:
                logger.warning(f"Post não encontrado (404): {url}")
                return _falha(url, "nao_encontrado")

            if resp.status_code in (403, 429):
                logger.warning(f"Acesso bloqueado ({resp.status_code}): {url}")
                return _falha(url, "acesso_restrito")

            html = resp.text

            # Detecta redirecionamento para página de login
            if any(ind in html.lower() for ind in _LOGIN_INDICATORS) and len(html) < 20000:
                logger.warning("Instagram redirecionou para página de login")
                return _falha(url, "acesso_restrito")

            og_description = self._meta(html, "og:description")
            og_title = self._meta(html, "og:title")
            og_type = self._meta(html, "og:type")

            caption = self._limpar_caption(og_description)
            perfil = self._perfil_url(og_title, url)
            tipo_midia_hint = "video" if og_type and "video" in og_type else "imagem"

            if caption:
                logger.info(f"Caption extraída ({len(caption)} chars): {caption[:120]}")
                return {
                    "caption": caption,
                    "url_publicacao": url,
                    "perfil_instagram": perfil,
                    "tipo_midia_hint": tipo_midia_hint,
                    "ok": True,
                    "motivo": "ok",
                }

            logger.warning("Caption vazia — post provavelmente privado ou sem texto")
            return _falha(url, "post_privado")

        except httpx.TimeoutException:
            logger.error(f"Timeout ao acessar: {url}")
            return _falha(url, "erro_rede")
        except Exception as e:
            logger.error(f"Erro no InstagramScraper: {e}")
            return _falha(url, "erro_rede")

    # ------------------------------------------------------------------

    def _meta(self, html: str, prop: str) -> str:
        """Extrai content de uma meta tag og: do HTML bruto."""
        for pat in (
            rf'<meta[^>]+property=["\']?{re.escape(prop)}["\']?[^>]+content=["\']([^"\']*)["\']',
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']?{re.escape(prop)}["\']?',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return self._decode(m.group(1))
        return ""

    def _decode(self, text: str) -> str:
        for ent, char in _HTML_ENTITIES.items():
            text = text.replace(ent, char)
        return text

    def _limpar_caption(self, og_description: str) -> str:
        """
        og:description vem como:
        "N Likes, N Comments - @usuario on Instagram: "caption aqui""
        Extrai só a parte da caption.
        """
        if not og_description:
            return ""
        m = re.search(
            r'on Instagram:\s*[\u201c"]?(.*)',
            og_description,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip().strip('"').strip("\u201d").strip()
        return og_description.strip()

    def _perfil_url(self, og_title: str, url: str) -> str:
        """Extrai URL do perfil a partir do título OG ou da URL do post."""
        m = re.search(r'@([\w.]+)', og_title)
        if m:
            return f"https://www.instagram.com/{m.group(1)}/"
        # URL formato: instagram.com/usuario/p/CODE/ ou instagram.com/p/CODE/
        m2 = re.search(r'instagram\.com/([^/p?#][^/?#]*)/(?:p|reel|tv)/', url)
        if m2:
            return f"https://www.instagram.com/{m2.group(1)}/"
        return ""


def _falha(url: str, motivo: str = "erro_rede") -> dict:
    return {
        "caption": "",
        "url_publicacao": url,
        "perfil_instagram": "",
        "tipo_midia_hint": "imagem",
        "ok": False,
        "motivo": motivo,
    }
