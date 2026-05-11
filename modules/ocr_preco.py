"""
OCR para extrair preço de imagens quando a IA não encontrou no texto.
Usa pytesseract (opcional). Se não instalado, retorna string vazia silenciosamente.

Instalação do Tesseract no Windows:
  https://github.com/UB-Mannheim/tesseract/wiki
  Marque "Portuguese" durante a instalação.
  Depois: pip install pytesseract
"""

import re
import unicodedata
import cv2
import numpy as np

from modules.logger import Logger

logger = Logger("ocr_preco")

# Tenta importar pytesseract — se não disponível, OCR é desabilitado
try:
    import pytesseract
    # Caminhos padrão do Tesseract no Windows
    _TESSERACT_PATHS = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\Public\Tesseract-OCR\tesseract.exe",
    ]
    import os as _os
    for _p in _TESSERACT_PATHS:
        if _os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break
    # Usa português se disponível, senão inglês (suficiente para números e R$)
    _langs = pytesseract.get_languages(config="")
    _OCR_LANG = "por+eng" if "por" in _langs else "eng"
    _OCR_OK = True
except ImportError:
    _OCR_OK = False
    logger.warning("pytesseract não instalado — OCR de preço desabilitado")

# Padrões de preço em português brasileiro
_PADROES_PRECO = [
    r'R\$\s*([\d.,]+)',                              # R$ 350.000,00
    r'(?:valor|pre[çc]o|venda)\s*:?\s*R?\$?\s*([\d.,]{4,})',  # Valor: 350.000
    r'\b(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?)\b',   # 350.000,00
]

_CIDADES_CONHECIDAS = {
    "joao pessoa": "João Pessoa",
    "cabedelo": "Cabedelo",
    "campina grande": "Campina Grande",
    "bayeux": "Bayeux",
    "santa rita": "Santa Rita",
    "conde": "Conde",
    "lucena": "Lucena",
    "pitimbu": "Pitimbu",
    "alhandra": "Alhandra",
    "mamanguape": "Mamanguape",
    "rio tinto": "Rio Tinto",
    "sape": "Sapé",
    "guarabira": "Guarabira",
    "patos": "Patos",
    "sousa": "Sousa",
    "cajazeiras": "Cajazeiras",
}

# Bairros mais recorrentes nos cards/imagens da PB. A cidade inferida daqui
# serve como reforço quando a legenda da imagem traz só o bairro.
_BAIRRO_CIDADE_PB = {
    # João Pessoa
    "manaira": "João Pessoa", "tambau": "João Pessoa", "cabo branco": "João Pessoa",
    "miramar": "João Pessoa", "bessa": "João Pessoa", "torre": "João Pessoa",
    "bancarios": "João Pessoa", "mangabeira": "João Pessoa", "valentina": "João Pessoa",
    "brisamar": "João Pessoa", "jardim oceania": "João Pessoa", "oceania": "João Pessoa",
    "estados": "João Pessoa", "bairro dos estados": "João Pessoa",
    "epitacio pessoa": "João Pessoa", "altiplano": "João Pessoa",
    "roger": "João Pessoa", "jaguaribe": "João Pessoa", "geisel": "João Pessoa",
    "cristo redentor": "João Pessoa", "castelo branco": "João Pessoa",
    "agua fria": "João Pessoa", "cruz das armas": "João Pessoa",
    "funcionarios": "João Pessoa", "expedicionarios": "João Pessoa",
    "tambia": "João Pessoa", "varadouro": "João Pessoa", "trincheiras": "João Pessoa",
    "pedro gondim": "João Pessoa", "aeroclube": "João Pessoa", "grotao": "João Pessoa",
    "cidade universitaria": "João Pessoa", "anatolia": "João Pessoa",
    "jose americo": "João Pessoa", "planalto": "João Pessoa",
    "cuia": "João Pessoa", "paratibe": "João Pessoa", "gramame": "João Pessoa",
    "mussumagro": "João Pessoa", "portal do sol": "João Pessoa",
    "costa e silva": "João Pessoa", "jose bezerra": "João Pessoa",
    "mandacaru": "João Pessoa", "san martin": "João Pessoa",
    "jardim luna": "João Pessoa", "alto do ceu": "João Pessoa",
    "penha": "João Pessoa", "ilha do bispo": "João Pessoa", "rangel": "João Pessoa",
    "13 de maio": "João Pessoa", "treze de maio": "João Pessoa",
    "jardim sao paulo": "João Pessoa", "padre ze": "João Pessoa",
    "conjunto ceara": "João Pessoa", "paulo vi": "João Pessoa",
    "novo horizonte joao pessoa": "João Pessoa",
    # Cabedelo
    "poco": "Cabedelo", "bairro do poco": "Cabedelo",
    "ponta de mato": "Cabedelo", "renascer": "Cabedelo",
    "ponta de cabedelo": "Cabedelo", "intermares": "Cabedelo",
    "camboinha": "Cabedelo", "praia de camboinha": "Cabedelo",
    "ponta de campina": "Cabedelo", "ponta campina": "Cabedelo",
    "naica": "Cabedelo", "formosa": "Cabedelo",
    "pocinhos cabedelo": "Cabedelo", "nova cabedelo": "Cabedelo",
    "jardins cabedelo": "Cabedelo", "camalau": "Cabedelo",
    "centro cabedelo": "Cabedelo",
    # Campina Grande
    "bodocongo": "Campina Grande", "jose pinheiro": "Campina Grande",
    "dinamarca": "Campina Grande", "liberdade": "Campina Grande",
    "sandra cavalcante": "Campina Grande", "malvinas": "Campina Grande",
    "catole": "Campina Grande", "prata": "Campina Grande",
    "bela vista campina": "Campina Grande", "palmeira campina": "Campina Grande",
    "universitario campina": "Campina Grande", "miriam coelho": "Campina Grande",
    "serrotao": "Campina Grande", "monte castelo": "Campina Grande",
    "centenario": "Campina Grande", "itarare": "Campina Grande",
}

_BAIRRO_CANONICO = {
    "manaira": "Manaíra",
    "tambau": "Tambaú",
    "bancarios": "Bancários",
    "anatolia": "Anatólia",
    "jose americo": "José Américo",
    "agua fria": "Água Fria",
    "funcionarios": "Funcionários",
    "expedicionarios": "Expedicionários",
    "tambia": "Tambiá",
    "grotao": "Grotão",
    "cidade universitaria": "Cidade Universitária",
    "alto do ceu": "Alto do Céu",
    "padre ze": "Padre Zé",
    "poco": "Poço",
    "bairro do poco": "Bairro do Poço",
    "catole": "Catolé",
    "bodocongo": "Bodocongó",
    "sape": "Sapé",
}


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


def extrair_localizacao_de_imagens(caminhos: list[str]) -> dict:
    """
    Tenta extrair bairro/cidade de textos gravados nas imagens.
    Retorna {"bairro_extraido": "...", "cidade_extraida": "..."} quando houver
    correspondência com nomes conhecidos, ou {} quando o OCR não for confiável.
    """
    if not _OCR_OK:
        return {}

    for caminho in caminhos[:3]:
        textos = _ocr_textos_imagem(caminho, limite_versoes=3)
        if not textos:
            continue
        localizacao = extrair_localizacao_de_texto("\n".join(textos))
        if localizacao:
            logger.info(f"OCR encontrou localização {localizacao} em {caminho}")
            return localizacao
    return {}


def _ocr_imagem(caminho: str) -> str:
    """Processa uma imagem e tenta extrair preço via OCR."""
    for texto in _ocr_textos_imagem(caminho):
        preco = _extrair_preco_texto(texto)
        if preco:
            return preco
    return ""


def _ocr_textos_imagem(caminho: str, limite_versoes: int | None = None) -> list[str]:
    """Processa uma imagem e retorna os textos lidos nas versões pré-processadas."""
    try:
        img = cv2.imread(caminho)
        if img is None:
            return []

        # Pré-processamento em múltiplas versões para maximizar leitura
        candidatos = _preprocessar(img)
        config = f"--psm 11 --oem 3 -l {_OCR_LANG}"
        textos = []

        for versao in candidatos[:limite_versoes]:
            texto = pytesseract.image_to_string(versao, config=config)
            if texto and texto.strip():
                textos.append(texto)
        return textos

    except Exception as e:
        logger.debug(f"Erro OCR em {caminho}: {e}")
    return []


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
            if 4 <= len(apenas_digitos) <= 8:
                return apenas_digitos
    return ""


def extrair_localizacao_de_texto(texto: str) -> dict:
    """Extrai localização de texto OCR já lido, com lista fechada de bairros/cidades."""
    texto_norm = _normalizar_ocr(texto)
    if not texto_norm:
        return {}

    cidades = _encontrar_cidades(texto_norm)
    bairros = _encontrar_bairros(texto_norm)
    if not cidades and bairros:
        cidades_bairros = {_BAIRRO_CIDADE_PB[b["chave"]] for b in bairros}
        if len(cidades_bairros) == 1:
            cidades = [(min(b["inicio"] for b in bairros), next(iter(cidades_bairros)))]

    resultado = {}
    if bairros:
        resultado["bairro_extraido"] = ", ".join(
            _bairro_canonico(b["chave"]) for b in bairros[:3]
        )
    if cidades:
        resultado["cidade_extraida"] = sorted(cidades, key=lambda item: item[0])[0][1]
    return resultado


def _normalizar_ocr(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    sem_acento = sem_acento.lower()
    sem_acento = re.sub(r"[^a-z0-9\s,/.-]+", " ", sem_acento)
    return re.sub(r"\s+", " ", sem_acento).strip()


def _padrao_termo(chave: str) -> str:
    partes = [re.escape(p) for p in chave.split()]
    corpo = r"\s+".join(partes)
    return rf"(?<![a-z0-9]){corpo}(?![a-z0-9])"


def _encontrar_cidades(texto_norm: str) -> list[tuple[int, str]]:
    encontrados = []
    for chave, cidade in _CIDADES_CONHECIDAS.items():
        for m in re.finditer(_padrao_termo(chave), texto_norm):
            encontrados.append((m.start(), cidade))
    return _deduplicar_por_nome(encontrados)


def _encontrar_bairros(texto_norm: str) -> list[dict]:
    matches = []
    for chave in sorted(_BAIRRO_CIDADE_PB, key=len, reverse=True):
        for m in re.finditer(_padrao_termo(chave), texto_norm):
            matches.append({"inicio": m.start(), "fim": m.end(), "chave": chave})

    filtrados = []
    for item in sorted(matches, key=lambda x: (x["inicio"], -(x["fim"] - x["inicio"]))):
        if any(item["inicio"] >= f["inicio"] and item["fim"] <= f["fim"] for f in filtrados):
            continue
        if any(f["chave"] == item["chave"] for f in filtrados):
            continue
        filtrados.append(item)
    return sorted(filtrados, key=lambda x: x["inicio"])


def _deduplicar_por_nome(itens: list[tuple[int, str]]) -> list[tuple[int, str]]:
    vistos = set()
    unicos = []
    for pos, nome in sorted(itens, key=lambda item: item[0]):
        if nome in vistos:
            continue
        vistos.add(nome)
        unicos.append((pos, nome))
    return unicos


def _bairro_canonico(chave: str) -> str:
    if chave in _BAIRRO_CANONICO:
        return _BAIRRO_CANONICO[chave]
    minusculas = {"da", "de", "do", "das", "dos", "e"}
    palavras = []
    for p in chave.split():
        palavras.append(p if p in minusculas else p.capitalize())
    return " ".join(palavras)
