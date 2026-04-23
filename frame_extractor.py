"""
Extrai frames estrategicos de video usando OpenCV.
Filtra frames tremidos, borrados, com pessoas, de transicao e muito parecidos.
Tenta preencher ate MAX_FRAMES com frames variados mostrando o imovel.
"""

import os
import re

import cv2
import numpy as np

from modules.logger import Logger

logger = Logger("frame_extractor")

MAX_FRAMES        = 20      # tenta preencher 20 uploads
PULAR_INICIO      = 0.04   # pula 4% do inicio (logos/apresentacao)
PULAR_FIM         = 0.04   # pula 4% do fim (créditos/contato)
BRILHO_MINIMO     = 30
BRILHO_MAXIMO     = 230
NITIDEZ_MINIMA    = 80     # exigente — descarta tremidos e panôramicas desfocadas
SIMILARIDADE_MAX  = 0.88   # histograma de cor — garante variedade de ambiente
AREA_PESSOA_MAX   = 0.20   # rejeita frame se pessoa ocupa > 20% da área
OFFSETS_BUSCA     = [0, -5, 5, -12, 12, -25, 25, -40, 40, -60, 60, -80, 80, -100, 100]

# HOG — detector de corpo inteiro
_hog = cv2.HOGDescriptor()
_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

# Haar — detector de face frontal (pega corretor de busto para cima)
_face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def extrair_frames(caminho_video: str, pasta_saida: str) -> list[str]:
    """
    Extrai frames de qualidade do video mostrando o imovel.
    Retorna lista de caminhos dos frames salvos (ate MAX_FRAMES).
    """
    cap = cv2.VideoCapture(caminho_video)
    if not cap.isOpened():
        logger.error(f"Nao foi possivel abrir o video: {caminho_video}")
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 30

    if total_frames <= 0:
        logger.error("Video sem frames detectados")
        cap.release()
        return []

    base_raw  = os.path.splitext(os.path.basename(caminho_video))[0]
    base_nome = re.sub(r"[^\w\s\-]", "", base_raw, flags=re.ASCII).strip()[:40] or "frame"

    frames_salvos    = []
    descritores_hist = []   # histogramas de cor para medir diversidade

    logger.info(
        f"Video: {total_frames} frames @ {fps:.1f}fps — "
        f"buscando ate {MAX_FRAMES} frames de qualidade"
    )

    # --- Capa: primeiros frames uteis apos o inicio ---
    frame_capa_limite = min(int(total_frames * 0.12), 60)
    for tentativa in range(frame_capa_limite):
        cap.set(cv2.CAP_PROP_POS_FRAMES, tentativa)
        ret, frame = cap.read()
        ok, motivo, hist = _frame_aproveitavel(frame) if ret else (False, "nao lido", None)
        if ok:
            caminho = os.path.join(pasta_saida, f"{base_nome}_frame_00_capa.jpg")
            cv2.imwrite(caminho, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            frames_salvos.append(caminho)
            descritores_hist.append(hist)
            logger.info(f"Capa: frame {tentativa}")
            break
        logger.debug(f"Capa candidata {tentativa} ignorada: {motivo}")

    # --- Corpo: divide video em segmentos e busca o melhor frame de cada ---
    frame_inicio = int(total_frames * PULAR_INICIO)
    frame_fim    = int(total_frames * (1 - PULAR_FIM))
    janela       = max(frame_fim - frame_inicio, 1)

    n_restantes      = min(MAX_FRAMES - len(frames_salvos), janela)
    tamanho_segmento = janela / n_restantes if n_restantes > 0 else 1

    for i in range(n_restantes):
        frame_alvo = int(frame_inicio + (i + 0.5) * tamanho_segmento)
        frame_alvo = min(frame_alvo, frame_fim - 1)

        frame, hist, motivo = _buscar_frame_bom(
            cap, frame_alvo, total_frames, descritores_hist
        )
        if frame is None:
            logger.debug(f"Segmento {i + 1}/{n_restantes} sem frame util: {motivo}")
            continue

        caminho = os.path.join(pasta_saida, f"{base_nome}_frame_{i + 1:02d}.jpg")
        cv2.imwrite(caminho, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        frames_salvos.append(caminho)
        descritores_hist.append(hist)
        logger.info(
            f"Segmento {i + 1}/{n_restantes} — frame {frame_alvo}: "
            f"{os.path.basename(caminho)}"
        )

    cap.release()
    logger.info(
        f"{len(frames_salvos)}/{MAX_FRAMES} frames extraidos de "
        f"{os.path.basename(caminho_video)}"
    )
    return frames_salvos


# ---------------------------------------------------------------------------
# Busca do melhor frame num segmento
# ---------------------------------------------------------------------------

def _buscar_frame_bom(cap, frame_alvo: int, total_frames: int, historicos: list):
    ultimo_motivo = "sem frame aproveitavel no segmento"
    for offset in OFFSETS_BUSCA:
        pos = min(max(frame_alvo + offset, 0), total_frames - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if not ret:
            ultimo_motivo = "nao lido"
            continue
        ok, motivo, hist = _frame_aproveitavel(frame)
        if not ok:
            ultimo_motivo = motivo
            continue
        if any(_similaridade_hist(hist, h) > SIMILARIDADE_MAX for h in historicos):
            ultimo_motivo = "muito parecido com frame ja salvo"
            continue
        return frame, hist, ""
    return None, None, ultimo_motivo


# ---------------------------------------------------------------------------
# Filtros de qualidade
# ---------------------------------------------------------------------------

def _frame_aproveitavel(frame):
    """Retorna (ok, motivo, histograma_cor) para o frame."""
    if frame is None:
        return False, "frame nulo", None

    # 1. Brilho global
    brilho = float(frame.mean())
    if brilho < BRILHO_MINIMO:
        return False, f"escuro ({brilho:.0f})", None
    if brilho > BRILHO_MAXIMO:
        return False, f"muito claro ({brilho:.0f})", None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # 2. Nitidez (Laplacian) — elimina tremidos e panôramicas desfocadas
    nitidez = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if nitidez < NITIDEZ_MINIMA:
        return False, f"borrado/tremido ({nitidez:.0f})", None

    # 3. Transição / fragmentação (excesso de bordas = frame de corte)
    edges = cv2.Canny(gray, 80, 160)
    densidade_bordas = float(np.mean(edges > 0))
    if densidade_bordas > 0.22:
        return False, f"transicao ({densidade_bordas:.2f})", None

    # 4. Pessoas: face + corpo
    motivo_pessoa = _detectar_pessoa(frame)
    if motivo_pessoa:
        return False, motivo_pessoa, None

    # 5. Histograma de cor (descritor de diversidade)
    hist = _histograma(frame)
    return True, "", hist


def _detectar_pessoa(frame) -> str:
    """
    Detecta face ou corpo no frame.
    Retorna string de motivo se encontrado, '' caso contrario.
    """
    h, w = frame.shape[:2]
    area_total = h * w

    # --- Face frontal (Haar, muito rapido) ---
    try:
        pequeno = cv2.resize(frame, (320, 180))
        gray_p  = cv2.cvtColor(pequeno, cv2.COLOR_BGR2GRAY)
        faces   = _face_cascade.detectMultiScale(
            gray_p, scaleFactor=1.1, minNeighbors=4, minSize=(20, 20)
        )
        if len(faces) > 0:
            # Escala a area detectada de volta ao tamanho original
            escala_x = w / 320
            escala_y = h / 180
            for (fx, fy, fw, fh) in faces:
                area_face = (fw * escala_x) * (fh * escala_y)
                if area_face / area_total > 0.03:   # face > 3% da imagem
                    return f"face detectada ({area_face/area_total:.0%} da area)"
    except Exception:
        pass

    # --- Corpo inteiro (HOG, mais pesado — so roda se nao achou face) ---
    try:
        pequeno  = cv2.resize(frame, (320, 180))
        rects, _ = _hog.detectMultiScale(
            pequeno, winStride=(8, 8), padding=(4, 4), scale=1.05
        )
        if len(rects) > 0:
            escala_x = w / 320
            escala_y = h / 180
            for (px, py, pw, ph) in rects:
                area_pessoa = (pw * escala_x) * (ph * escala_y)
                if area_pessoa / area_total > AREA_PESSOA_MAX:
                    return f"corpo detectado ({area_pessoa/area_total:.0%} da area)"
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# Histograma de cor para diversidade entre frames
# ---------------------------------------------------------------------------

def _histograma(frame) -> np.ndarray:
    """Histograma normalizado de cor HSV (16 bins por canal H e S)."""
    hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h_hist = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
    s_hist = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
    desc   = np.concatenate([h_hist, s_hist]).astype("float32")
    norm   = np.linalg.norm(desc)
    return desc / norm if norm else desc


def _similaridade_hist(a: np.ndarray, b: np.ndarray) -> float:
    """Correlacao entre dois histogramas (0 = diferente, 1 = identico)."""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))
