"""
Extração de mídia do Instagram via Apify API.
Substitui o FastDL browser como método primário — 3-8s por post, estável em escala.

Fallback chain (definida no MediaResolver):
  Apify → yt-dlp → FastDL browser
"""

import os
import re
import asyncio
import httpx
from modules.logger import Logger
from modules.frame_extractor import extrair_frames

logger = Logger("instagram_media_api")

# Actor IDs disponíveis no Apify Marketplace
ACTOR_OFICIAL   = "apify~instagram-scraper"                # actor principal — suporta directUrls
ACTOR_LOWCOST   = "sones~instagram-posts-scraper-lowcost"  # $0,30/1k posts — testar estabilidade

APIFY_RUN_URL   = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
DOWNLOAD_TIMEOUT = 60   # segundos por arquivo
API_TIMEOUT      = 90   # timeout total da chamada Apify


class ApifyMediaExtractor:
    def __init__(self, token: str, downloads_path: str, actor_id: str = ""):
        self.token          = token
        self.downloads_path = downloads_path
        self.actor_id       = actor_id.strip() or ACTOR_OFICIAL

    async def extrair_post(self, url_instagram: str) -> dict:
        """
        Extrai dados do post (caption, perfil) via Apify.
        Retorna dict compatível com InstagramScraper.extrair().
        """
        try:
            item = await self._chamar_apify(url_instagram)
        except Exception as e:
            logger.warning(f"Apify post: {e}")
            return {"ok": False, "motivo": "erro_rede"}

        if not item:
            return {"ok": False, "motivo": "nao_encontrado"}

        caption = (
            item.get("caption")
            or item.get("description")
            or item.get("alt")
            or ""
        )
        perfil = item.get("ownerUsername") or item.get("username") or ""

        logger.info(f"Apify: caption extraído ({len(caption)} chars)")
        return {
            "ok": True,
            "caption": caption,
            "url_publicacao": url_instagram,
            "perfil_instagram": perfil,
        }

    async def extrair(self, url_instagram: str) -> tuple[str, list[str]]:
        """
        Extrai mídia de um post do Instagram via Apify.
        Retorna (tipo_midia, lista_arquivos) — tipo_midia: 'imagem' ou 'video'.
        Retorna ('imagem', []) em caso de falha.
        """
        logger.info(f"Apify: extraindo {url_instagram}")
        try:
            item = await self._chamar_apify(url_instagram)
        except Exception as e:
            logger.warning(f"Apify falhou: {e}")
            return "imagem", []

        if not item:
            logger.warning("Apify: nenhum resultado retornado")
            return "imagem", []

        tipo       = self._detectar_tipo(item)
        media_urls = self._extrair_urls(item, tipo)

        if not media_urls:
            logger.warning("Apify: nenhuma URL de mídia no resultado")
            return tipo, []

        logger.info(f"Apify: {len(media_urls)} URL(s) encontrada(s) [{tipo}]")
        arquivos = await self._baixar_todos(media_urls, tipo)
        return tipo, arquivos

    # ------------------------------------------------------------------
    # Chamada à API Apify
    # ------------------------------------------------------------------

    async def _chamar_apify(self, url: str) -> dict | None:
        endpoint = APIFY_RUN_URL.format(actor=self.actor_id)
        payload  = {"directUrls": [url], "resultsLimit": 1}
        params   = {"token": self.token}

        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            r = await client.post(endpoint, json=payload, params=params)

        if r.status_code not in (200, 201):
            raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")

        items = r.json()
        if not items:
            return None

        return items[0] if isinstance(items, list) else items

    # ------------------------------------------------------------------
    # Parsing do resultado Apify
    # ------------------------------------------------------------------

    def _detectar_tipo(self, item: dict) -> str:
        tipo = str(item.get("type") or item.get("media_type") or "").lower()
        if "video" in tipo:
            return "video"
        if item.get("videoUrl") or item.get("video_url"):
            return "video"
        return "imagem"

    def _extrair_urls(self, item: dict, tipo: str) -> list[str]:
        urls = []
        if tipo == "video":
            # Campos possíveis dependendo do actor
            for campo in ("videoUrl", "video_url", "videoUrls", "video_urls"):
                val = item.get(campo)
                if isinstance(val, list):
                    urls.extend(u for u in val if u)
                elif val:
                    urls.append(val)
                if urls:
                    break
            # Thumbnail como fallback para extração de frames
            if not urls:
                thumb = item.get("displayUrl") or item.get("thumbnailUrl") or ""
                if thumb:
                    urls.append(thumb)
        else:
            # Carrossel: campo images ou imageUrls
            for campo in ("images", "imageUrls", "image_urls"):
                val = item.get(campo)
                if isinstance(val, list) and val:
                    urls.extend(u for u in val if u)
                    break
            # Post simples: displayUrl
            if not urls:
                main = item.get("displayUrl") or item.get("display_url") or ""
                if main:
                    urls.append(main)

        return [u for u in urls if u and u.startswith("http")]

    # ------------------------------------------------------------------
    # Download dos arquivos
    # ------------------------------------------------------------------

    async def _baixar_todos(self, urls: list[str], tipo: str) -> list[str]:
        os.makedirs(self.downloads_path, exist_ok=True)
        tasks   = [self._baixar_url(url, i, tipo) for i, url in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        arquivos = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Falha ao baixar: {r}")
            elif r:
                arquivos.append(r)

        if tipo == "video" and arquivos:
            # Extrai frames do primeiro vídeo e descarta o arquivo original
            caminho_video = arquivos[0]
            frames = extrair_frames(caminho_video, self.downloads_path)
            try:
                os.remove(caminho_video)
            except Exception:
                pass
            return frames if frames else []

        return arquivos

    async def _baixar_url(self, url: str, indice: int, tipo: str) -> str | None:
        ext    = ".mp4" if tipo == "video" else ".jpg"
        nome   = f"apify_{indice + 1:02d}{ext}"
        destino = os.path.join(self.downloads_path, nome)

        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(destino, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
            logger.info(f"Baixado: {nome} ({os.path.getsize(destino) // 1024} KB)")
            return destino
        except Exception as e:
            logger.warning(f"Erro ao baixar {url[:80]}: {e}")
            try:
                os.remove(destino)
            except Exception:
                pass
            return None


# ------------------------------------------------------------------
# Extração via yt-dlp (fallback 2)
# ------------------------------------------------------------------

async def extrair_via_ytdlp(url: str, downloads_path: str) -> tuple[str, list[str]]:
    """
    Tenta baixar mídia do Instagram com yt-dlp.
    Retorna (tipo_midia, lista_arquivos).
    """
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        logger.warning("yt-dlp não instalado — pulando fallback")
        return "imagem", []

    os.makedirs(downloads_path, exist_ok=True)
    output_template = os.path.join(downloads_path, "ytdlp_%(autonumber)s.%(ext)s")

    ydl_opts = {
        "outtmpl":          output_template,
        "quiet":            True,
        "no_warnings":      True,
        "ignoreerrors":     True,
        "writesubtitles":   False,
        "noplaylist":       True,
        "format":           "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        # Sem cookies — se a conta for privada, yt-dlp também falha
    }

    arquivos_antes = set(os.listdir(downloads_path))

    loop = asyncio.get_running_loop()
    erro = None
    try:
        def _run():
            import yt_dlp
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info

        info = await loop.run_in_executor(None, _run)
    except Exception as e:
        erro = e

    arquivos_depois = set(os.listdir(downloads_path))
    novos = [
        os.path.join(downloads_path, f)
        for f in (arquivos_depois - arquivos_antes)
        if not f.endswith((".part", ".ytdl", ".json"))
    ]

    if not novos:
        logger.warning(f"yt-dlp: nenhum arquivo baixado{f' ({erro})' if erro else ''}")
        return "imagem", []

    logger.info(f"yt-dlp: {len(novos)} arquivo(s) baixado(s)")

    # Detecta tipo pelo conteúdo baixado
    tem_video = any(f.endswith(".mp4") for f in novos)
    if tem_video:
        videos = [f for f in novos if f.endswith(".mp4")]
        frames = extrair_frames(videos[0], downloads_path)
        for v in videos:
            try:
                os.remove(v)
            except Exception:
                pass
        return "video", frames if frames else []

    # Imagens — filtra arquivos válidos
    imagens = [f for f in novos if re.search(r"\.(jpe?g|png|webp)$", f, re.I)]
    return "imagem", imagens
