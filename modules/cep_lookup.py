"""
Busca conservadora de CEP a partir dos dados de endereco do imovel.
Funciona para todas as fontes: OLX, Instagram (DeepSeek) e Orulo.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

import httpx

from modules.logger import Logger

logger = Logger("cep_lookup")


# Mapeamento cidade normalizada → UF (Paraíba e arredores)
_CIDADE_UF: dict[str, str] = {
    # Paraíba
    "joao pessoa": "PB", "cabedelo": "PB", "campina grande": "PB",
    "bayeux": "PB", "santa rita": "PB", "conde": "PB", "lucena": "PB",
    "pitimbu": "PB", "alhandra": "PB", "mamanguape": "PB",
    "rio tinto": "PB", "sape": "PB", "guarabira": "PB", "patos": "PB",
    "sousa": "PB", "cajazeiras": "PB", "monteiro": "PB",
    # Pernambuco
    "recife": "PE", "olinda": "PE", "caruaru": "PE", "petrolina": "PE",
    "paulista": "PE", "jaboatao dos guararapes": "PE", "jaboatao": "PE",
    # Rio Grande do Norte
    "natal": "RN", "mossoro": "RN", "parnamirim": "RN", "caicó": "RN",
    # Ceará
    "fortaleza": "CE", "caucaia": "CE", "juazeiro do norte": "CE",
    # Bahia
    "salvador": "BA", "feira de santana": "BA", "ilheus": "BA",
    # São Paulo
    "sao paulo": "SP", "campinas": "SP", "santos": "SP", "guarulhos": "SP",
    # Rio de Janeiro
    "rio de janeiro": "RJ", "niteroi": "RJ",
}


def _norm(texto: str) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_cep(valor: str) -> str:
    digits = re.sub(r"\D", "", str(valor or ""))
    if len(digits) != 8:
        return ""
    return f"{digits[:5]}-{digits[5:]}"


def _cep_explicito(dados: dict) -> str:
    """Retorna CEP se já estiver nos dados (explícito ou na descrição)."""
    for campo in ("cep", "codigo_postal", "postal_code", "zip"):
        cep = normalizar_cep(dados.get(campo, ""))
        if cep:
            return cep
    # Procura padrão CEP no endereço ou descrição
    texto = "\n".join(str(dados.get(k, "")) for k in ("endereco", "descricao_util", "proximidades"))
    for m in re.finditer(r"\bCEP\s*:?\s*(\d{5}[-\s]?\d{3})\b", texto, re.IGNORECASE):
        cep = normalizar_cep(m.group(1))
        if cep:
            return cep
    return ""


def _uf(dados: dict) -> str:
    """Extrai a UF (sigla do estado) dos dados do imóvel."""
    # 1. Campo explícito de UF/estado
    raw = str(dados.get("uf") or dados.get("estado") or dados.get("estado_extraido") or "").upper()
    m = re.search(r"\b([A-Z]{2})\b", raw)
    if m:
        return m.group(1)

    # 2. Formato "Cidade/UF" (Orulo, DeepSeek) ex: "João Pessoa/PB"
    cidade_raw = str(dados.get("cidade_extraida") or "").strip()
    m2 = re.search(r"/([A-Z]{2})\s*$", cidade_raw)
    if m2:
        return m2.group(1)

    # 3. Lookup pela cidade (sem a parte "/UF" se houver)
    cidade_nome = cidade_raw.split("/")[0].strip()
    return _CIDADE_UF.get(_norm(cidade_nome), "")


def _cidade_limpa(dados: dict) -> str:
    """Retorna o nome da cidade sem o sufixo '/UF'."""
    cidade = str(dados.get("cidade_extraida") or "").strip()
    return cidade.split("/")[0].strip()


def _logradouro(endereco: str) -> str:
    """Extrai só o nome da rua, removendo número, bairro e cidade."""
    texto = str(endereco or "").strip()
    # Remove CEP inline
    texto = re.sub(r"\bCEP\s*:?\s*\d{5}[-\s]?\d{3}\b", "", texto, flags=re.IGNORECASE)
    # Corta no número do imóvel (ex: ", 800" ou " - 800") mas não em "500m²" ou "2º andar"
    texto = re.split(r",\s*\d+\b|\s+-\s*\d+\b", texto, maxsplit=1)[0]
    # Corta no bairro (após vírgula)
    texto = texto.split(",")[0]
    return re.sub(r"\s+", " ", texto).strip(" ,.-")


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


async def _consultar_viacep(uf: str, cidade: str, logradouro: str, dados: dict) -> str:
    cidade_sem_uf = cidade.split("/")[0].strip()
    if not (uf and cidade_sem_uf and len(logradouro) >= 3):
        return ""
    url = f"https://viacep.com.br/ws/{quote(uf)}/{quote(cidade_sem_uf)}/{quote(logradouro)}/json/"
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        itens = resp.json()
        if not isinstance(itens, list) or not itens:
            return ""
        itens = sorted(itens, key=lambda item: _score(item, dados, logradouro), reverse=True)
        cep = normalizar_cep(itens[0].get("cep", ""))
        if cep:
            logger.info(f"CEP encontrado: {cep} ({itens[0].get('logradouro', '')}, {itens[0].get('bairro', '')})")
        return cep
    except Exception as e:
        logger.debug(f"Busca de CEP falhou ({logradouro}): {e}")
        return ""


async def buscar_cep(dados: dict) -> str:
    """Busca o CEP correto para o imóvel. Funciona com OLX, Instagram e Orulo."""
    # Usa o CEP que já veio nos dados (se válido)
    cep = _cep_explicito(dados)
    if cep:
        return cep

    endereco = str(dados.get("endereco") or dados.get("rua") or "").strip()
    cidade = _cidade_limpa(dados)
    uf = _uf(dados)
    logradouro = _logradouro(endereco)

    if not (uf and cidade):
        logger.debug(f"Busca CEP ignorada: UF={uf!r}, cidade={cidade!r}")
        return ""

    # 1ª tentativa: pelo logradouro (rua extraída do endereço)
    if logradouro:
        cep = await _consultar_viacep(uf, cidade, logradouro, dados)
        if cep:
            return cep

    # 2ª tentativa: pelo bairro (quando não há rua mas há bairro)
    bairro = str(dados.get("bairro_extraido") or "").strip()
    if bairro and bairro != logradouro:
        cep = await _consultar_viacep(uf, cidade, bairro, dados)
        if cep:
            logger.info(f"CEP via bairro: {bairro}")
            return cep

    # 3ª tentativa: nome do empreendimento/condomínio
    empreendimento = str(
        dados.get("_empreendimento_resumo") or dados.get("titulo") or ""
    ).strip()
    empreendimento = re.split(r"[,\-(]", empreendimento)[0].strip()
    if empreendimento and empreendimento not in (logradouro, bairro) and len(empreendimento) >= 5:
        cep = await _consultar_viacep(uf, cidade, empreendimento, dados)
        if cep:
            logger.info(f"CEP via empreendimento: {empreendimento}")
            return cep

    return ""
