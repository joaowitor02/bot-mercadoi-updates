"""
Extrai frames estratégicos de um vídeo usando OpenCV.
Até 20 frames distribuídos uniformemente, pulando intro/créditos e frames escuros.
"""

import re
import cv2
import os
from modules.logger import Logger

logger = Logger("frame_extractor")

MAX_FRAMES = 20
PULAR_INICIO = 0.05   # pular primeiros 5%
PULAR_FIM = 0.03      # pular últimos 3%
BRILHO_MINIMO = 20    # ignorar frames muito escuros (0-255)


def extrair_frames(caminho_video: str, pasta_saida: str) -> list[str]:
    """
    Extrai até MAX_FRAMES frames estratégicos do vídeo.
    Retorna lista de caminhos dos frames salvos.
    """
    cap = cv2.VideoCapture(caminho_video)
    if not cap.isOpened():
        logger.error(f"Não foi possível abrir o vídeo: {caminho_video}")
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    if total_frames <= 0:
        logger.error("Vídeo sem frames detectados")
        cap.release()
        return []

    base_raw = os.path.splitext(os.path.basename(caminho_video))[0]
    base_nome = re.sub(r'[^\w\s\-]', '', base_raw, flags=re.ASCII).strip()[:40] or "frame"
    frames_salvos = []

    logger.info(f"Vídeo: {total_frames} frames @ {fps:.1f}fps — extraindo até {MAX_FRAMES} frames")

    # 1. Sempre extrair a capa (primeiro frame útil do vídeo)
    for tentativa_capa in range(min(10, total_frames)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, tentativa_capa)
        ret, frame = cap.read()
        if ret and frame.mean() >= BRILHO_MINIMO:
            caminho_capa = os.path.join(pasta_saida, f"{base_nome}_frame_00_capa.jpg")
            cv2.imwrite(caminho_capa, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frames_salvos.append(caminho_capa)
            logger.info(f"Capa extraída (frame {tentativa_capa}): {os.path.basename(caminho_capa)}")
            break

    # 2. Frames distribuídos pelo restante do vídeo (pulando intro/créditos)
    frame_inicio = int(total_frames * PULAR_INICIO)
    frame_fim = int(total_frames * (1 - PULAR_FIM))
    janela = max(frame_fim - frame_inicio, 1)

    n_restantes = min(MAX_FRAMES - len(frames_salvos), janela)
    tamanho_segmento = janela / n_restantes if n_restantes > 0 else 1

    for i in range(n_restantes):
        frame_alvo = int(frame_inicio + (i + 0.5) * tamanho_segmento)
        frame_alvo = min(frame_alvo, frame_fim - 1)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_alvo)
        ret, frame = cap.read()
        if not ret:
            continue

        brilho = frame.mean()
        if brilho < BRILHO_MINIMO:
            logger.debug(f"Frame {i+1} pulado (muito escuro: {brilho:.1f})")
            continue

        caminho_saida = os.path.join(pasta_saida, f"{base_nome}_frame_{i+1:02d}.jpg")
        cv2.imwrite(caminho_saida, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        frames_salvos.append(caminho_saida)
        logger.info(f"Frame {i+1}/{n_restantes} salvo: {os.path.basename(caminho_saida)}")

    cap.release()
    logger.info(f"{len(frames_salvos)} frames extraídos de {os.path.basename(caminho_video)}")
    return frames_salvos
