"""
OCR para extrair preço de imagens quando a IA não encontrou no texto.
Usa pytesseract (opcional). Se não instalado, retorna string vazia silenciosamente.

Instalação do Tesseract no Windows:
  https://github.com/UB-Mannheim/tesseract/wiki
  Marque "Portuguese" durante a instalação.
  Depois: pip install pytesseract
"""

import re
import cv2
import numpy as np

from modules.logger import Logger

logger = Logger("ocr_preco")

# Tenta importar pytesseract — se não disponível, OCR é desabilitado
try:
    import pytesseract
    _OCR_OK = True
except ImportError:
    _OCR_OK = False
    logger.warning("pytesseract não instalado — OCR de preço desabilitado")

# Padrões de preço em português brasileiro
_PADROES_PRECO = [
    r'R\$\s*([\d.,]+)',                              # R$ 350.000,00
    r'(?:valor|pre[çc]o|venda)\s*:?\s*R?\$?\s*([\d.,]{4,})',  # Valor: 350.000
    r'\b(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?)\b',   # 350.000,00
    r'\b(\d{5,})\b',                                 # 350000
]


def extrair_preco_de_imagens(caminhos: list[str]) -> str:
    """
    Tenta extrair preço de uma lista de imagens via OCR.
    Retorna string com apenas dígitos (ex: '350000') ou '' se não encontrar.
    Processa no máximo 3 imagens para não demorar.
    """
    if not _OCR_OK:
        return ""

    for caminho in caminhos[:3]:
        preco = _ocr_imagem(caminho)
        if preco:
            logger.info(f"OCR encontrou preço '{preco}' em {caminho}")
            return preco
    return ""


def _ocr_imagem(caminho: str) -> str:
    """Processa uma imagem e tenta extrair preço via OCR."""
    try:
        img = cv2.imread(caminho)
        if img is None:
            return ""

        # Pré-processamento em múltiplas versões para maximizar leitura
        candidatos = _preprocessar(img)
        config = "--psm 11 --oem 3 -l por+eng"

        for versao in candidatos:
            texto = pytesseract.image_to_string(versao, config=config)
            preco = _extrair_preco_texto(texto)
            if preco:
                return preco

    except Exception as e:
        logger.debug(f"Erro OCR em {caminho}: {e}")
    return ""


def _preprocessar(img: np.ndarray) -> list:
    """
    Gera versões pré-processadas da imagem para melhorar o OCR.
    Foco em regiões com texto de preço (inferior, superior, centro).
    """
    h, w = img.shape[:2]
    versoes = []

    # Redimensiona para altura mínima razoável
    escala = max(1.0, 800 / h)
    if escala > 1.0:
        img = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
        h, w = img.shape[:2]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Imagem completa em escala de cinza
    versoes.append(gray)

    # 2. Terço inferior (onde preço costuma aparecer em overlays)
    versoes.append(gray[h * 2 // 3:, :])

    # 3. Terço superior
    versoes.append(gray[:h // 3, :])

    # 4. Threshold adaptativo (texto branco/preto em fundo variado)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 2
    )
    versoes.append(thresh)

    # 5. Invertido (texto preto em fundo branco → inverso para texto claro)
    versoes.append(cv2.bitwise_not(thresh))

    # 6. Contraste aumentado via CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    versoes.append(enhanced)

    return versoes


def _extrair_preco_texto(texto: str) -> str:
    """Aplica regex para encontrar padrão de preço e retorna só os dígitos."""
    if not texto:
        return ""
    for padrao in _PADROES_PRECO:
        match = re.search(padrao, texto, re.IGNORECASE)
        if match:
            raw = match.group(1)
            # Remove decimais (,00 ou .00) e separa apenas inteiro
            raw = re.sub(r'[,\.]\d{2}$', '', raw.strip())
            apenas_digitos = re.sub(r'[^\d]', '', raw)
            # Valida: preço imobiliário tem entre 4 e 10 dígitos
            if 4 <= len(apenas_digitos) <= 10:
                return apenas_digitos
    return ""
