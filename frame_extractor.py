"""
Extrai frames estrategicos de video usando OpenCV.
Filtra frames muito parecidos, borrados, brancos, escuros ou de transicao.
"""

import os
import re

import cv2
import numpy as np

from modules.logger import Logger

logger = Logger("frame_extractor")

MAX_FRAMES = 12
PULAR_INICIO = 0.05
PULAR_FIM = 0.03
BRILHO_MINIMO = 20
BRILHO_MAXIMO = 238
NITIDEZ_MINIMA = 45
SIMILARIDADE_MAXIMA = 0.965

# Detector de pessoas (HOG + SVM — sem dependência externa)
_hog = cv2.HOGDescriptor()
_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())


def extrair_frames(caminho_video: str, pasta_saida: str) -> list[str]:
    """
    Extrai frames bons e variados do video.
    Retorna lista de caminhos dos frames salvos.
    """
    cap = cv2.VideoCapture(caminho_video)
    if not cap.isOpened():
        logger.error(f"Nao foi possivel abrir o video: {caminho_video}")
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    if total_frames <= 0:
        logger.error("Video sem frames detectados")
        cap.release()
        return []

    base_raw = os.path.splitext(os.path.basename(caminho_video))[0]
    base_nome = re.sub(r"[^\w\s\-]", "", base_raw, flags=re.ASCII).strip()[:40] or "frame"
    frames_salvos = []
    descritores_salvos = []

    logger.info(f"Video: {total_frames} frames @ {fps:.1f}fps - extraindo ate {MAX_FRAMES} frames")

    for tentativa_capa in range(min(20, total_frames)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, tentativa_capa)
        ret, frame = cap.read()
        ok, motivo, desc = _frame_aproveitavel(frame) if ret else (False, "nao lido", None)
        if ok:
            caminho_capa = os.path.join(pasta_saida, f"{base_nome}_frame_00_capa.jpg")
            cv2.imwrite(caminho_capa, frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            frames_salvos.append(caminho_capa)
            descritores_salvos.append(desc)
            logger.info(f"Capa extraida (frame {tentativa_capa}): {os.path.basename(caminho_capa)}")
            break
        logger.debug(f"Capa candidata {tentativa_capa} ignorada: {motivo}")

    frame_inicio = int(total_frames * PULAR_INICIO)
    frame_fim = int(total_frames * (1 - PULAR_FIM))
    janela = max(frame_fim - frame_inicio, 1)

    n_restantes = min(MAX_FRAMES - len(frames_salvos), janela)
    tamanho_segmento = janela / n_restantes if n_restantes > 0 else 1

    for i in range(n_restantes):
        frame_alvo = int(frame_inicio + (i + 0.5) * tamanho_segmento)
        frame_alvo = min(frame_alvo, frame_fim - 1)

        frame, desc, motivo = _buscar_frame_bom(cap, frame_alvo, total_frames, descritores_salvos)
        if frame is None:
            logger.debug(f"Segmento {i + 1} pulado: {motivo}")
            continue

        caminho_saida = os.path.join(pasta_saida, f"{base_nome}_frame_{i + 1:02d}.jpg")
        cv2.imwrite(caminho_saida, frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        frames_salvos.append(caminho_saida)
        descritores_salvos.append(desc)
        logger.info(f"Frame {i + 1}/{n_restantes} salvo: {os.path.basename(caminho_saida)}")

    cap.release()
    logger.info(f"{len(frames_salvos)} frames extraidos de {os.path.basename(caminho_video)}")
    return frames_salvos


def _buscar_frame_bom(cap, frame_alvo: int, total_frames: int, descritores_salvos: list):
    offsets = [0, -8, 8, -16, 16, -30, 30, -45, 45, -60, 60]
    ultimo_motivo = "sem frame aproveitavel"
    for offset in offsets:
        pos = min(max(frame_alvo + offset, 0), total_frames - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if not ret:
            ultimo_motivo = "nao lido"
            continue
        ok, motivo, desc = _frame_aproveitavel(frame)
        if not ok:
            ultimo_motivo = motivo
            continue
        if any(_similaridade(desc, salvo) > SIMILARIDADE_MAXIMA for salvo in descritores_salvos):
            ultimo_motivo = "muito parecido com frame ja salvo"
            continue
        return frame, desc, ""
    return None, None, ultimo_motivo


def _tem_pessoa(frame) -> bool:
    """Retorna True se o HOG detectar pelo menos uma pessoa no frame."""
    try:
        pequeno = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
        rects, _ = _hog.detectMultiScale(
            pequeno, winStride=(8, 8), padding=(4, 4), scale=1.05,
        )
        return len(rects) > 0
    except Exception:
        return False


def _frame_aproveitavel(frame):
    brilho = float(frame.mean())
    if brilho < BRILHO_MINIMO:
        return False, f"muito escuro: {brilho:.1f}", None
    if brilho > BRILHO_MAXIMO:
        return False, f"muito claro/branco: {brilho:.1f}", None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    nitidez = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if nitidez < NITIDEZ_MINIMA:
        return False, f"borrado: {nitidez:.1f}", None

    edges = cv2.Canny(gray, 80, 160)
    densidade_bordas = float(np.mean(edges > 0))
    if densidade_bordas > 0.24:
        return False, f"transicao/fragmentado: {densidade_bordas:.2f}", None

    if _tem_pessoa(frame):
        return False, "pessoa detectada", None

    pequeno = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    desc = pequeno.astype("float32").flatten()
    desc -= desc.mean()
    norm = np.linalg.norm(desc)
    if norm:
        desc /= norm
    return True, "", desc


def _similaridade(a, b) -> float:
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))
