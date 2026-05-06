"""
Busca conservadora de CEP a partir dos dados de endereco do imovel.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from modules.logger import Logger

logger = Logger("cep_lookup")


_CIDADE_UF = {
    "joao pessoa": "PB",
    "cabedelo": "PB",
    "campina grande": "PB",
    "bayeux": "PB",
    "santa rita": "PB",
    "conde": "PB",
    "lucena": "PB",
    "pitimbu": "PB",
    "alhandra": "PB",
    "mamanguape": "PB",
}


def _norm(texto: str) -> str:
    try:
        import unicodedata

        texto = unicodedata.normalize("NFD", str(texto or "").lower())
        texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    except Exception:
        texto = str(texto or "").lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_cep(valor: str) -> str:
    digits = re.sub(r"\D", "", str(valor or ""))
    if len(digits) != 8:
        return ""
    return f"{digits[:5]}-{digits[5:]}"


def _cep_explicito(dados: dict) -> str:
    for campo in ("cep", "codigo_postal", "postal_code", "zip"):
        cep = normalizar_cep(dados.get(campo, ""))
        if cep:
            return cep
    texto = "\n".join(str(dados.get(k, "")) for k in ("endereco", "descricao_util", "proximidades"))
    for m in re.finditer(r"\b\d{5}[-\s]?\d{3}\b", texto):
        cep = normalizar_cep(m.group(0))
        if cep:
            return cep
    return ""


def _uf(dados: dict) -> str:
    raw = str(dados.get("uf") or dados.get("estado") or dados.get("estado_extraido") or "").upper()
    m = re.search(r"\b[A-Z]{2}\b", raw)
    if m:
        return m.group(0)
    cidade = _norm(dados.get("cidade_extraida", ""))
    return _CIDADE_UF.get(cidade, "")


def _logradouro(endereco: str) -> str:
    texto = str(endereco or "").strip()
    texto = re.sub(r"\bCEP\s*:?\s*\d{5}[-\s]?\d{3}\b", "", texto, flags=re.IGNORECASE)
    texto = re.split(r"\s*[,;-]\s*\d+\b|\s+\d+\b", texto, maxsplit=1)[0]
    texto = re.sub(r"\s+", " ", texto).strip(" ,.-")
    return texto


def _score(item: dict, dados: dict, logradouro: str) -> int:
    score = 0
    bairro = _norm(dados.get("bairro_extraido", ""))
    item_bairro = _norm(item.get("bairro", ""))
    item_logradouro = _norm(item.get("logradouro", ""))
    logradouro_norm = _norm(logradouro)
    if bairro and item_bairro == bairro:
        score += 4
    elif bairro and (bairro in item_bairro or item_bairro in bairro):
        score += 2
    if logradouro_norm and item_logradouro == logradouro_norm:
        score += 4
    elif logradouro_norm and (logradouro_norm in item_logradouro or item_logradouro in logradouro_norm):
        score += 2
    return score


async def buscar_cep(dados: dict) -> str:
    cep = _cep_explicito(dados)
    if cep:
        return cep

    endereco = str(dados.get("endereco") or dados.get("rua") or "").strip()
    cidade = str(dados.get("cidade_extraida") or "").strip()
    uf = _uf(dados)
    logradouro = _logradouro(endereco)
    if not (uf and cidade and len(logradouro) >= 3):
        return ""

    url = f"https://viacep.com.br/ws/{quote(uf)}/{quote(cidade)}/{quote(logradouro)}/json/"
    try:
        async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        itens = resp.json()
        if not isinstance(itens, list) or not itens:
            return ""
        itens = sorted(itens, key=lambda item: _score(item, dados, logradouro), reverse=True)
        cep = normalizar_cep(itens[0].get("cep", ""))
        if cep:
            logger.info(f"CEP encontrado: {cep} ({itens[0].get('logradouro', '')})")
        return cep
    except Exception as e:
        logger.debug(f"Busca de CEP falhou: {e}")
        return ""
