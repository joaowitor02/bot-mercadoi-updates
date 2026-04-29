"""
Resolve mídia do Instagram com cadeia de fallback:
  1. Apify API       — rápido (3-8s), pago (~R$150/mês para 300/dia)
  2. yt-dlp          — gratuito, mas bloqueado em alto volume
  3. FastDL browser  — lento (30-60s), fallback final

Ativa o Apify quando config tiver apify_api_token preenchido e usar_apify=true.
"""

import asyncio
import os
import re
from urllib.parse import urljoin

from playwright.async_api import async_playwright
from PIL import Image
from pillow_heif import register_heif_opener

from modules.logger import Logger
from modules.instagram_media_api import ApifyMediaExtractor, extrair_via_ytdlp

logger = Logger("media_resolver")
register_heif_opener()

FASTDL_URL = "https://fastdl.app/pt/"


class MediaResolver:
    def __init__(self, downloads_path: str, config: dict | None = None):
        self.downloads_path = downloads_path
        self._config        = config or {}

    async def resolver(self, url_instagram: str) -> tuple[str, list[str]]:
        """
        Resolve mídia com fallback automático: Apify → yt-dlp → FastDL.
        Retorna (tipo_midia, lista_arquivos).
        """
        cfg = self._config

        # --- Tentativa 1: Apify API ---
        if cfg.get("usar_apify") and cfg.get("apify_api_token", "").strip():
            logger.info("Tentativa 1: Apify API")
            extrator = ApifyMediaExtractor(
                token          = cfg["apify_api_token"],
                downloads_path = self.downloads_path,
                actor_id       = cfg.get("apify_actor_id", ""),
            )
            tipo, arquivos = await extrator.extrair(url_instagram)
            if arquivos:
                logger.info(f"Apify OK: {len(arquivos)} arquivo(s)")
                return tipo, arquivos
            logger.warning("Apify não retornou arquivos — tentando yt-dlp")

        # --- Tentativa 2: yt-dlp ---
        logger.info("Tentativa 2: yt-dlp")
        tipo, arquivos = await extrair_via_ytdlp(url_instagram, self.downloads_path)
        if arquivos:
            logger.info(f"yt-dlp OK: {len(arquivos)} arquivo(s)")
            return tipo, arquivos
        logger.warning("yt-dlp não retornou arquivos — tentando FastDL")

        # --- Tentativa 3: FastDL browser (legado) ---
        logger.info("Tentativa 3: FastDL browser")
        for tentativa in range(1, 3):
            resultado = await self._via_fastdl(url_instagram)
            tipo, arquivos = resultado
            if arquivos:
                return resultado
            if tentativa < 2:
                logger.warning(f"FastDL vazio na tentativa {tentativa}/2 — aguardando 2s...")
                await asyncio.sleep(2)

        logger.error("Todas as tentativas de download falharam")
        return "imagem", []

    async def _via_fastdl(self, url_instagram: str) -> tuple[str, list[str]]:
        """Download via FastDL browser (fallback final)."""
        import sys
        headless = sys.platform != "win32"
        logger.info(f"Acessando FastDL para: {url_instagram} (headless={headless})")
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"] if headless else [],
                )
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()

                await page.goto(FASTDL_URL, timeout=30000)

                await page.wait_for_selector('input[type="text"], input[placeholder*="nstagram"], textarea', timeout=10000)
                campo = await page.query_selector('input[type="text"], input[placeholder*="nstagram"], textarea')
                await campo.fill(url_instagram)
                await page.click('button[type="submit"], button:has-text("Download"), button:has-text("Baixar")')

                # Aguarda resultados aparecerem em vez de espera fixa
                try:
                    await page.wait_for_selector(
                        'a[href*="media.fastdl.app"], a[href*="fastdl.app/get"], '
                        'a[download], video, a[href*=".mp4"], a[href*=".jpg"], a[href*=".jpeg"]',
                        timeout=15000
                    )
                except Exception:
                    await page.wait_for_timeout(2000)  # fallback mínimo

                tipo = await self._detectar_tipo(page)
                logger.info(f"Tipo de midia detectado: {tipo}")

                if tipo == "video":
                    video = await self._baixar_video(page, context)
                    await browser.close()
                    browser = None
                    if video:
                        from modules.frame_extractor import extrair_frames

                        frames = extrair_frames(video, self.downloads_path)
                        try:
                            os.remove(video)
                            logger.info(f"Video removido apos extracao: {os.path.basename(video)}")
                        except Exception as e:
                            logger.warning(f"Nao foi possivel remover video: {e}")
                        if frames:
                            return "video", frames
                    return "video", []

                arquivos = await self._baixar_imagens(page, context)
                await browser.close()
                browser = None

                if arquivos:
                    logger.info(f"{len(arquivos)} imagem(ns) baixada(s)")
                    return "imagem", arquivos

                logger.error("Nenhuma imagem baixada")
                return "imagem", []

        except Exception as e:
            logger.error(f"Erro no MediaResolver: {e}")
            return "imagem", []
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _baixar_video(self, page, context) -> str | None:
        """Baixa o arquivo de video da pagina de resultados."""
        try:
            seletores = [
                'a[href*=".mp4"]',
                'a[download][href*="video"]',
                'a:has-text("Baixar video")',
                'a:has-text("Baixar vídeo")',
                'a:has-text("Download video")',
                'a:has-text("Download MP4")',
            ]
            for sel in seletores:
                elem = await page.query_selector(sel)
                if elem:
                    await self._remover_bloqueios(page)
                    async with page.expect_download(timeout=60000) as dl_info:
                        await elem.evaluate("el => el.click()")
                    download = await dl_info.value
                    nome_raw, ext = os.path.splitext(download.suggested_filename or "video.mp4")
                    nome = re.sub(r"[^\w\s\-]", "", nome_raw, flags=re.ASCII).strip()[:60] or "video"
                    destino = os.path.join(self.downloads_path, f"{nome}{ext}")
                    await download.save_as(destino)
                    logger.info(f"Video baixado: {destino}")
                    return destino
            logger.warning("Nenhum link de video encontrado")
            return None
        except Exception as e:
            logger.error(f"Erro ao baixar video: {e}")
            return None

    async def _detectar_tipo(self, page) -> str:
        try:
            video_elem = await page.query_selector('video, a[href*=".mp4"], a[download*="video"]')
            return "video" if video_elem else "imagem"
        except Exception:
            return "imagem"

    async def _baixar_imagens(self, page, context) -> list[str]:
        """Baixa todas as imagens disponiveis na pagina de resultados."""
        arquivos = []

        botoes = await self._aguardar_botoes_imagem(page)
        if not botoes:
            logger.warning("Nenhum botao de download encontrado")
            return []

        logger.info(f"Encontrados {len(botoes)} botoes de imagem")

        for i, item in enumerate(botoes):
            try:
                await self._remover_bloqueios(page)
                await page.wait_for_timeout(80)
                async with page.expect_download(timeout=30000) as download_info:
                    clicou = await page.evaluate(
                        """
                        ({href, index}) => {
                            const links = Array.from(document.querySelectorAll('a[href]'));
                            const byHref = links.find(el => el.href === href || el.getAttribute('href') === href);
                            const byIndex = links.find(el => el.dataset.botDownloadIndex === String(index));
                            const alvo = byHref || byIndex;
                            if (!alvo) return false;
                            alvo.scrollIntoView({ block: 'center', inline: 'center' });
                            alvo.click();
                            return true;
                        }
                        """,
                        item,
                    )
                    if not clicou:
                        raise Exception("Botao de download nao encontrado no DOM")

                download = await download_info.value
                nome_raw, ext = os.path.splitext(download.suggested_filename or f"imagem_{i + 1}.jpg")
                nome = re.sub(r"[^\w\s\-]", "", nome_raw, flags=re.ASCII).strip()[:60] or f"imagem_{i + 1}"
                nome_unico = f"{nome}_{i + 1}{ext}"
                destino = os.path.join(self.downloads_path, nome_unico)
                await download.save_as(destino)
                destino = self._converter_para_jpg_se_necessario(destino)
                arquivos.append(destino)
                logger.info(f"Imagem {i + 1}/{len(botoes)} baixada: {destino}")
                await page.wait_for_timeout(100)
            except Exception as e:
                logger.warning(f"Erro ao baixar imagem {i + 1}: {e}")

        return arquivos

    def _converter_para_jpg_se_necessario(self, caminho: str) -> str:
        """Converte HEIC/HEIF/WebP/PNG para JPG quando o Mercadoi pode rejeitar o upload."""
        ext = os.path.splitext(caminho)[1].lower()
        if ext in (".jpg", ".jpeg"):
            return caminho
        if ext not in (".heic", ".heif", ".webp", ".png"):
            return caminho

        destino = os.path.splitext(caminho)[0] + ".jpg"
        try:
            with Image.open(caminho) as img:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")
                img.save(destino, "JPEG", quality=92, optimize=True)
            try:
                os.remove(caminho)
            except Exception:
                pass
            logger.info(f"Imagem convertida para JPG: {destino}")
            return destino
        except Exception as e:
            logger.warning(f"Nao foi possivel converter {os.path.basename(caminho)} para JPG: {e}")
            return caminho

    async def _remover_bloqueios(self, page):
        """Remove anuncios/modais que costumam interceptar cliques no FastDL."""
        await page.evaluate(
            """
            () => {
                document.querySelectorAll(
                    'ins.adsbygoogle, .ad-modal, [id^="aswift_"], iframe[id^="aswift_"], div[role="dialog"]'
                ).forEach(el => el.remove());
            }
            """
        )

    async def _aguardar_botoes_imagem(self, page, timeout_ms: int = 30000) -> list[dict]:
        """
        Aguarda a lista de downloads estabilizar.
        Rola a página completa para forçar lazy-load e para quando estável por 3 iterações.
        """
        melhor = []
        melhor_qtd = 0
        estavel = 0
        iteracao = 0
        inicio = asyncio.get_running_loop().time()

        while (asyncio.get_running_loop().time() - inicio) * 1000 < timeout_ms:
            await self._remover_bloqueios(page)
            # Rola em 5 passos para ativar lazy-load de todas as imagens
            await page.evaluate(
                """
                async () => {
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const maxY = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                    for (const p of [0, 0.25, 0.5, 0.75, 1]) {
                        window.scrollTo(0, Math.floor(maxY * p));
                        await sleep(350);
                    }
                    window.scrollTo(0, maxY);
                }
                """
            )
            await page.wait_for_timeout(500)
            iteracao += 1

            botoes = await self._coletar_botoes_imagem(page)
            qtd = len(botoes)
            if qtd > melhor_qtd:
                melhor = botoes
                melhor_qtd = qtd
                estavel = 0
                logger.info(f"FastDL: {qtd} botao(oes) de imagem detectado(s)")
            else:
                estavel += 1

            # Mínimo 2 iterações antes de parar (garante lazy-load completo)
            # Para quando estável por 3 iterações seguidas ou encontrou 10+
            if iteracao >= 2 and melhor_qtd > 0 and estavel >= 3:
                break
            if melhor_qtd >= 10 and estavel >= 1:
                break

        await page.evaluate("window.scrollTo(0, 0)")
        return melhor

    async def _coletar_botoes_imagem(self, page) -> list[dict]:
        base_url = page.url
        itens = await page.evaluate(
            """
            () => {
                const vistos = new Set();
                const itens = [];
                const links = Array.from(document.querySelectorAll('a[href]'));

                links.forEach((el, index) => {
                    const hrefRaw = el.getAttribute('href') || '';
                    const href = el.href || hrefRaw;
                    const texto = (el.innerText || el.textContent || '').toLowerCase();
                    const download = el.getAttribute('download') || '';
                    const lower = href.toLowerCase();

                    if (!hrefRaw || lower.startsWith('javascript:') || lower.includes('.mp4')) return;

                    const pareceDownload =
                        download !== '' ||
                        lower.includes('media.fastdl.app/get') ||
                        /\\.(jpe?g|png|webp)(\\?|#|$)/i.test(href);

                    if (!pareceDownload) return;

                    const chave = href || `${download}:${index}`;
                    if (vistos.has(chave)) return;
                    vistos.add(chave);
                    el.dataset.botDownloadIndex = String(index);
                    itens.push({ href, hrefRaw, download, index });
                });

                return itens;
            }
            """
        )

        normalizados = []
        vistos = set()
        for item in itens:
            href = urljoin(base_url, item.get("href") or item.get("hrefRaw") or "")
            if not href or href in vistos:
                continue
            vistos.add(href)
            normalizados.append(
                {
                    "href": href,
                    "index": item.get("index", len(normalizados)),
                    "download": item.get("download", ""),
                }
            )
        return normalizados
