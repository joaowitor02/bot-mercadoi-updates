"""
Busca conservadora de CEP a partir dos dados de endereco do imovel.
Funciona para todas as fontes: OLX, Instagram (DeepSeek) e Orulo.
"""

from __future__ import annotations

import asyncio
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


# Prefixos de CEP válidos por UF (primeiros 2 dígitos)
_UF_CEP_PREFIXOS: dict[str, tuple] = {
    "PB": ("58",),
    "PE": ("50", "51", "52", "53", "54", "55", "56"),
    "RN": ("59",),
    "CE": ("60", "61", "62", "63"),
    "AL": ("57",),
    "SE": ("49",),
    "BA": ("40", "41", "42", "43", "44", "45", "46", "47", "48"),
    "MA": ("65",),
    "PI": ("64",),
    "PA": ("66", "67", "68"),
    "SP": ("01", "02", "03", "04", "05", "06", "07", "08", "09",
           "10", "11", "12", "13", "14", "15", "16", "17", "18", "19"),
    "RJ": ("20", "21", "22", "23", "24", "25", "26", "27", "28"),
    "MG": ("30", "31", "32", "33", "34", "35", "36", "37", "38", "39"),
    "ES": ("29",),
    "PR": ("80", "81", "82", "83", "84", "85", "86", "87"),
    "SC": ("88", "89"),
    "RS": ("90", "91", "92", "93", "94", "95", "96", "97", "98", "99"),
    "DF": ("70", "71", "72", "73"),
    "GO": ("72", "73", "74", "75", "76"),
}


def _cep_valido_para_uf(cep: str, uf: str) -> bool:
    """Verifica se os primeiros dígitos do CEP são compatíveis com a UF."""
    digits = re.sub(r"\D", "", cep)
    if len(digits) != 8:
        return False
    prefixos = _UF_CEP_PREFIXOS.get(uf.upper())
    if not prefixos:
        return True  # UF sem mapeamento → aceita
    return any(digits.startswith(p) for p in prefixos)


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
    """Extrai só o nome da rua, removendo número e bairro."""
    texto = str(endereco or "").strip()
    # Remove CEP inline
    texto = re.sub(r"\bCEP\s*:?\s*\d{5}[-\s]?\d{3}\b", "", texto, flags=re.IGNORECASE)
    # Corta no número do imóvel seguido de ponto/vírgula/bairro
    # Ex: "Rua X 1240. Jardim Y" → "Rua X 1240"
    texto = re.split(r"\.\s+[A-ZÀ-Ú]", texto, maxsplit=1)[0]
    # Corta no número seguido de vírgula/traço
    texto = re.split(r",\s*\d+\b|\s+-\s*\d+\b", texto, maxsplit=1)[0]
    # Corta no bairro após vírgula
    texto = texto.split(",")[0]
    # Remove número do imóvel isolado no final (ex: "Rua X 1240")
    texto = re.sub(r"\s+\d+\s*$", "", texto)
    return re.sub(r"\s+", " ", texto).strip(" ,.-")


def _extrair_bairro_do_endereco(endereco: str) -> str:
    """Extrai o bairro quando está embutido no endereço após ponto/vírgula."""
    texto = str(endereco or "").strip()
    # Formato: "Rua X, 100, Bairro Y" ou "Rua X 100. Bairro Y"
    # Funciona com ou sem maiúsculas iniciais
    m = re.search(r"[,.]\s+([A-ZÀ-Úa-zA-ZÀ-ú][a-zA-ZÀ-ú\s]{3,50})$", texto, re.IGNORECASE)
    if m:
        candidato = m.group(1).strip()
        # Descarta se parece cidade ("João Pessoa") ou tem só 1 palavra curta
        if (len(candidato) >= 4
                and not re.search(r"\b(joao pessoa|recife|fortaleza|salvador|sao paulo)\b", candidato, re.IGNORECASE)):
            return candidato
    return ""


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


async def buscar_logradouro_por_cep(cep: str) -> str:
    """Consulta o ViaCEP com o CEP e retorna o nome da rua (logradouro)."""
    digits = re.sub(r"\D", "", str(cep or ""))
    if len(digits) != 8:
        return ""
    url = f"https://viacep.com.br/ws/{digits}/json/"
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        if not isinstance(data, dict) or data.get("erro"):
            return ""
        logradouro = data.get("logradouro", "").strip()
        if logradouro:
            logger.info(f"Logradouro via CEP {cep}: {logradouro}")
        return logradouro
    except Exception as e:
        logger.debug(f"Busca logradouro por CEP falhou ({cep}): {e}")
        return ""


async def buscar_cep(dados: dict) -> str:
    """Busca o CEP correto para o imóvel. Funciona com OLX, Instagram e Orulo."""
    uf = _uf(dados)

    # CEP explícito nos dados — valida se é compatível com a UF conhecida
    cep = _cep_explicito(dados)
    if cep:
        if uf and not _cep_valido_para_uf(cep, uf):
            logger.info(f"CEP {cep} invalido para UF={uf} — descartado, buscando via ViaCEP")
            cep = ""
        else:
            return cep

    endereco = str(dados.get("endereco") or dados.get("rua") or "").strip()
    cidade = _cidade_limpa(dados)
    logradouro = _logradouro(endereco)

    if not (uf and cidade):
        logger.debug(f"Busca CEP ignorada: UF={uf!r}, cidade={cidade!r}")
        return ""

    # Tenta logradouro, bairro e empreendimento em PARALELO (asyncio.gather)
    # Antes: sequencial com timeout 5s cada = até 15s. Agora: máx 5s total.
    bairro = str(dados.get("bairro_extraido") or "").strip()
    empreendimento = str(
        dados.get("_empreendimento_resumo") or dados.get("titulo") or ""
    ).strip()
    empreendimento = re.split(r"[,\-(]", empreendimento)[0].strip()

    termos: list[tuple[str, str]] = []  # (termo, label)
    if logradouro:
        termos.append((logradouro, "logradouro"))
    if bairro and bairro != logradouro:
        termos.append((bairro, "bairro"))
    if empreendimento and empreendimento not in (logradouro, bairro) and len(empreendimento) >= 5:
        termos.append((empreendimento, "empreendimento"))

    if not termos:
        return ""

    resultados = await asyncio.gather(
        *[_consultar_viacep(uf, cidade, t, dados) for t, _ in termos],
        return_exceptions=True,
    )
    for (_, label), cep in zip(termos, resultados):
        if isinstance(cep, str) and cep:
            if label != "logradouro":
                logger.info(f"CEP via {label}")
            return cep

    return ""
