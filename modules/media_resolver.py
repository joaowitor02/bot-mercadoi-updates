"""
Módulo para resolver mídia via FastDL.
Detecta se a mídia é imagem ou vídeo e baixa todas as imagens disponíveis.
"""

import asyncio
import os
import re
from playwright.async_api import async_playwright
from modules.logger import Logger

logger = Logger("media_resolver")

FASTDL_URL = "https://fastdl.app/pt/"


class MediaResolver:
    def __init__(self, downloads_path: str):
        self.downloads_path = downloads_path

    async def resolver(self, url_instagram: str, max_tentativas: int = 2) -> tuple[str, list[str]]:
        """
        Acessa o FastDL com a URL do Instagram.
        Retorna (tipo_midia, lista_arquivos) onde tipo_midia é 'imagem' ou 'video'.
        Faz até max_tentativas em caso de falha transitória.
        """
        for tentativa in range(1, max_tentativas + 1):
            resultado = await self._resolver_uma_vez(url_instagram)
            tipo, arquivos = resultado
            if arquivos or tentativa == max_tentativas:
                return resultado
            logger.warning(f"Mídia vazia na tentativa {tentativa}/{max_tentativas} — aguardando 5s...")
            await asyncio.sleep(5)
        return "imagem", []

    async def _resolver_uma_vez(self, url_instagram: str) -> tuple[str, list[str]]:
        """Tentativa única de resolver mídia via FastDL."""
        logger.info(f"Acessando FastDL para: {url_instagram}")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False)
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()

                await page.goto(FASTDL_URL, timeout=30000)

                await page.wait_for_selector('input[type="text"], input[placeholder*="nstagram"], textarea', timeout=10000)
                campo = await page.query_selector('input[type="text"], input[placeholder*="nstagram"], textarea')
                await campo.fill(url_instagram)
                await page.click('button[type="submit"], button:has-text("Download"), button:has-text("Baixar")')
                await page.wait_for_timeout(5000)

                tipo = await self._detectar_tipo(page)
                logger.info(f"Tipo de mídia detectado: {tipo}")

                if tipo == "video":
                    video = await self._baixar_video(page, context)
                    await browser.close()
                    if video:
                        from modules.frame_extractor import extrair_frames
                        frames = extrair_frames(video, self.downloads_path)
                        try:
                            os.remove(video)
                            logger.info(f"Vídeo removido após extração: {os.path.basename(video)}")
                        except Exception as e:
                            logger.warning(f"Não foi possível remover vídeo: {e}")
                        if frames:
                            return "video", frames
                    return "video", []

                arquivos = await self._baixar_imagens(page, context)
                await browser.close()

                if arquivos:
                    logger.info(f"{len(arquivos)} imagem(ns) baixada(s)")
                    return "imagem", arquivos
                else:
                    logger.error("Nenhuma imagem baixada")
                    return "imagem", []

        except Exception as e:
            logger.error(f"Erro no MediaResolver: {e}")
            return "imagem", []

    async def _baixar_video(self, page, context) -> str | None:
        """Baixa o arquivo de vídeo da página de resultados."""
        try:
            seletores = [
                'a[href*=".mp4"]',
                'a[download][href*="video"]',
                'a:has-text("Baixar vídeo")',
                'a:has-text("Download video")',
                'a:has-text("Download MP4")',
            ]
            for sel in seletores:
                elem = await page.query_selector(sel)
                if elem:
                    await page.evaluate("""
                        () => {
                            document.querySelectorAll('ins.adsbygoogle, .ad-modal, [id^="aswift_"]').forEach(el => el.remove());
                        }
                    """)
                    async with page.expect_download(timeout=60000) as dl_info:
                        await elem.evaluate("el => el.click()")
                    download = await dl_info.value
                    nome_raw, ext = os.path.splitext(download.suggested_filename or "video.mp4")
                    nome = re.sub(r'[^\w\s\-]', '', nome_raw, flags=re.ASCII).strip()[:60] or "video"
                    destino = os.path.join(self.downloads_path, f"{nome}{ext}")
                    await download.save_as(destino)
                    logger.info(f"Vídeo baixado: {destino}")
                    return destino
            logger.warning("Nenhum link de vídeo encontrado")
            return None
        except Exception as e:
            logger.error(f"Erro ao baixar vídeo: {e}")
            return None

    async def _detectar_tipo(self, page) -> str:
        try:
            video_elem = await page.query_selector('video, a[href*=".mp4"], a[download*="video"]')
            return "video" if video_elem else "imagem"
        except Exception:
            return "imagem"

    async def _baixar_imagens(self, page, context) -> list[str]:
        """Baixa todas as imagens disponíveis na página de resultados."""
        arquivos = []

        # Scroll para forçar carregamento lazy de todos os itens
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

        # Coletar hrefs únicos de todos os links de download de imagem
        hrefs_vistos = set()
        botoes = []

        # Varrer todos os <a> com atributo download que não sejam mp4
        todos = await page.query_selector_all('a[href]')
        for el in todos:
            href = await el.get_attribute('href') or ''
            dl = await el.get_attribute('download')
            if dl is None:
                continue
            if '.mp4' in href.lower():
                continue
            chave = href or id(el)
            if chave in hrefs_vistos:
                continue
            hrefs_vistos.add(chave)
            botoes.append(el)

        if botoes:
            logger.info(f"Encontrados {len(botoes)} botões de imagem")
        else:
            logger.warning("Nenhum botão de download encontrado")
            return []

        if not botoes:
            logger.warning("Nenhum botão de download encontrado")
            return []

        for i, botao in enumerate(botoes):
            try:
                # Fechar modal de anuncio se existir
                await page.evaluate("""
                    () => {
                        const modal = document.querySelector('div[role="dialog"].ad-modal');
                        if (modal) modal.remove();
                        // Remover iframes de anuncio que bloqueiam cliques
                        document.querySelectorAll('ins.adsbygoogle, .ad-modal, [id^="aswift_"]').forEach(el => el.remove());
                    }
                """)
                await page.wait_for_timeout(300)
                async with page.expect_download(timeout=30000) as download_info:
                    await botao.evaluate("el => el.click()")
                download = await download_info.value
                nome_raw, ext = os.path.splitext(download.suggested_filename or f"imagem_{i+1}.jpg")
                nome = re.sub(r'[^\w\s\-]', '', nome_raw, flags=re.ASCII).strip()[:60] or f"imagem_{i+1}"
                nome_unico = f"{nome}_{i+1}{ext}"
                destino = os.path.join(self.downloads_path, nome_unico)
                await download.save_as(destino)
                arquivos.append(destino)
                logger.info(f"Imagem {i+1}/{len(botoes)} baixada: {destino}")
                await page.wait_for_timeout(500)
            except Exception as e:
                logger.warning(f"Erro ao baixar imagem {i+1}: {e}")

        return arquivos
