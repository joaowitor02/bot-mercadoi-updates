"""
Regras compartilhadas para caracteristicas marcadas automaticamente.
"""

import re
import unicodedata


def normalizar_caracteristica(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    sem_sinais = re.sub(r"[^a-zA-Z0-9]+", " ", sem_acento.lower())
    return re.sub(r"\s+", " ", sem_sinais).strip()


NUNCA_MARCAR: set[str] = {normalizar_caracteristica(x) for x in [
    # Areas comuns/privativas que o fluxo nao deve marcar automaticamente.
    "Bar",
    "Banheiro social",
    "Biblioteca",
    "Circuito de seguranca",
    "Camera de seguranca",
    "Portaria 24h",
    "Portaria eletronica",
    "Segurança 24h",
    "Segurança 24 horas",
    "Spa",
    "Terraco",
    "Terraco Rooftop",
    "Rooftop",
    "Sistema de alarme",
    "Piscina infantil",
    "Piscina Privativa",
    "Deck",
    "Deck molhado",
    "Elevador social",
]}


CARACTERISTICAS_TEXTO = [
    ("area de lazer", "Área de Lazer"),
    ("parque aquatico", "Piscina adulto"),
    ("parque aquatico", "Área de Lazer"),
    ("ar condicionado", "Ar Condicionado"),
    ("varanda gourmet", "Varanda gourmet"),
    ("varanda", "Varanda"),
    ("area de servico", "Área de serviço"),
    ("cozinha americana", "Cozinha americana"),
    ("cozinha gourmet", "Cozinha Gourmet"),
    ("area gourmet", "Espaço gourmet"),
    ("espaco gourmet", "Espaço gourmet"),
    ("moveis planejados", "Projetados"),
    ("projetados", "Projetados"),
    ("piscina adulto", "Piscina adulto"),
    ("piscina", "Piscina adulto"),
    ("hidromassagem", "Hidromassagem"),
    ("banheira de hidromassagem", "Hidromassagem"),
    ("jacuzzi", "Hidromassagem"),
    ("churrasqueira", "Churrasqueira"),
    ("espaco grill", "Churrasqueira"),
    ("vaga coberta", "Vaga coberta"),
    ("vagas cobertas", "Vaga coberta"),
    ("vaga de garagem coberta", "Vaga coberta"),
    ("vagas de garagem cobertas", "Vaga coberta"),
    ("garagem coberta", "Vaga coberta"),
    ("academia", "Academia"),
    ("playground", "Playground"),
    ("area pet", "Área pet / Espaço pet"),
    ("espaco pet", "Área pet / Espaço pet"),
    ("portaria", "Portaria"),
    ("mini mercado", "Mini mercado"),
    ("minimercado", "Mini mercado"),
    ("mini market", "Mini mercado"),
    # "market" foi removido — corresponde a "supermarket", "marketing", etc. (falso positivo)
    ("lavanderia", "Lavanderia"),
    ("coworking", "Coworking"),
    ("brinquedoteca", "Brinquedoteca"),
    ("espaco kids", "Espaço kids / Brinquedoteca"),
    ("seguranca", "Segurança"),
    ("salao de festas", "Salão de festas"),
    ("sala de festas", "Salão de festas"),
    ("salao de festas e jogos", "Sala de jogos"),
    ("sala de festas e jogos", "Sala de jogos"),
    ("salao de festa e jogos", "Sala de jogos"),
    ("sala de festa e jogos", "Sala de jogos"),
    ("sala de jogos", "Sala de jogos"),
    ("salao de jogos", "Sala de jogos"),
    ("quadra de areia", "Quadra de areia"),
    ("quadras de areia", "Quadra de areia"),
    ("quadra poliesportiva", "Quadra poliesportiva"),
    ("quadras poliesportiva", "Quadra poliesportiva"),
    ("quadras poliesportivas", "Quadra poliesportiva"),
    ("quadras de areia e poliesportiva", "Quadra de areia"),
    ("quadras de areia e poliesportiva", "Quadra poliesportiva"),
    ("lounge", "Lounge"),
    ("louge", "Lounge"),
    ("solarium", "Solarium"),
]


CANONICAS: dict[str, str] = {
    normalizar_caracteristica("Piscina"): "Piscina adulto",
    normalizar_caracteristica("Espaço Gourmet"): "Espaço gourmet",
    normalizar_caracteristica("Salão de jogos"): "Sala de jogos",
    normalizar_caracteristica("Salao de jogos"): "Sala de jogos",
    normalizar_caracteristica("Salão de festas / SUM"): "Salão de festas",
    normalizar_caracteristica("Salao de festas / SUM"): "Salão de festas",
    normalizar_caracteristica("Hidromassagem/Jacuzzi"): "Hidromassagem",
    normalizar_caracteristica("Área pet / Espaço pet"): "Área pet / Espaço pet",
    normalizar_caracteristica("Espaço kids / Brinquedoteca"): "Espaço kids / Brinquedoteca",
}


# Regex pré-compilados (compilados 1x no load, não a cada chamada)
_NUNCA_MARCAR_RE: list[re.Pattern] = [
    re.compile(rf"(^| ){re.escape(b)}($| )") for b in NUNCA_MARCAR
]
_LINHA_BLOQUEADA_RE: list[re.Pattern] = [re.compile(p) for p in [
    r"\bportaria\s*24\b",
    r"\bseguranca\s*24\b",
    r"\bpiscinas?\b.*\binfantil\b",
    r"\belevador\s+social\b",
    r"\bdeck(?:\s+molhado)?\b",
    r"\bterraco(?:\s+rooftop)?\b",
    r"\brooftop\b",
    r"\bspa\b",
    r"\bsistema\s+de\s+alarme\b",
    r"\bcircuito\s+de\s+seguranca\b",
    r"\bcamera(?:s)?\s+de\s+seguranca\b",
]]


def caracteristica_bloqueada(caracteristica: str) -> bool:
    cn = normalizar_caracteristica(caracteristica)
    return any(p.search(cn) for p in _NUNCA_MARCAR_RE)


def _tem_termo_bloqueado(texto: str) -> bool:
    tn = normalizar_caracteristica(texto)
    return any(p.search(tn) for p in _NUNCA_MARCAR_RE)


def _linha_tem_termo_bloqueado(linha: str) -> bool:
    if _tem_termo_bloqueado(linha):
        return True
    ln = normalizar_caracteristica(linha)
    return any(p.search(ln) for p in _LINHA_BLOQUEADA_RE)


def contem_termo_normalizado(texto_norm: str, termo_norm: str) -> bool:
    return bool(re.search(rf"(^| ){re.escape(termo_norm)}($| )", texto_norm))


def limpar_descricao_bloqueada(descricao: str) -> str:
    """Remove contato solto e itens restritos da descricao exibida."""
    texto = str(descricao or "")
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)/?\S*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"@\w+", "", texto)
    telefone_re = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}")

    linhas = []
    vazio_anterior = False
    for linha in texto.splitlines():
        limpa = linha.strip()
        if not limpa:
            if linhas and not vazio_anterior:
                linhas.append("")
                vazio_anterior = True
            continue
        if re.search(r"(instagram|whatsapp|telefone|celular|contato)\s*:?", limpa, re.IGNORECASE):
            continue
        if telefone_re.search(limpa):
            continue
        if _linha_tem_termo_bloqueado(limpa):
            continue
        linhas.append(limpa)
        vazio_anterior = False
    return "\n".join(linhas).strip()


def _deve_adicionar_caracteristica(chave: str, texto_norm: str) -> bool:
    if chave == "portaria":
        limpo = texto_norm
        for termo in ("portaria 24 horas", "portaria 24h", "portaria 24", "portaria eletronica"):
            limpo = limpo.replace(termo, " ")
        return bool(re.search(r"(^| )portaria($| )", limpo))
    if chave == "seguranca":
        limpo = texto_norm
        for termo in (
            "circuito de seguranca",
            "camera de seguranca",
            "cameras de seguranca",
            "seguranca 24 horas",
            "seguranca 24h",
            "seguranca 24",
            "seguranca eletronica",
            "sistema de alarme",
        ):
            limpo = limpo.replace(termo, " ")
        return bool(re.search(r"(^| )seguranca($| )", limpo))
    if chave == "piscina":
        return "piscina infantil" not in texto_norm or "piscina adulto" in texto_norm
    return True


def _origem_exige_evidencia(dados: dict) -> bool:
    fonte = normalizar_caracteristica(dados.get("_fonte") or "")
    url = str(dados.get("url_publicacao") or dados.get("instagram_url") or "").lower()
    return fonte == "instagram" or "instagram.com" in url


def _termos_evidencia(nome: str) -> list[str]:
    nome_norm = normalizar_caracteristica(nome)
    termos = [
        normalizar_caracteristica(chave)
        for chave, destino in CARACTERISTICAS_TEXTO
        if normalizar_caracteristica(destino) == nome_norm
    ]
    if nome_norm == "salao de festas sum":
        termos.extend(["salao de festas", "sala de festas", "sum"])
    if nome_norm == "hidromassagem jacuzzi":
        termos.extend(["hidromassagem", "jacuzzi", "banheira de hidromassagem"])
    if nome_norm == "espaco kids":
        termos.extend(["espaco kids", "brinquedoteca"])
    if nome_norm == "churrasqueira propria":
        termos.extend(["churrasqueira propria", "churrasqueira privativa"])
    return list(dict.fromkeys(t for t in termos if t))


def caracteristica_tem_evidencia(nome: str, texto_norm: str) -> bool:
    if not nome or caracteristica_bloqueada(nome):
        return False
    for termo in _termos_evidencia(nome):
        if contem_termo_normalizado(texto_norm, termo) and _deve_adicionar_caracteristica(termo, texto_norm):
            return True
    return False


def adicionar_caracteristica(caracteristicas: list, nome: str) -> None:
    if nome and not caracteristica_bloqueada(nome) and nome not in caracteristicas:
        caracteristicas.append(nome)


def enriquecer_caracteristicas_por_texto(dados: dict) -> dict:
    """Completa caracteristicas por titulo/descricao, usado por OLX, Orulo e Instagram."""
    dados = dict(dados or {})
    descricao_original = str(dados.get("descricao_util") or "")
    descricao_limpa = limpar_descricao_bloqueada(descricao_original)
    texto_norm = normalizar_caracteristica("\n".join([
        str(dados.get("titulo") or ""),
        descricao_limpa,
    ]))
    exige_evidencia = _origem_exige_evidencia(dados)
    caracteristicas = []
    for nome in filtrar_caracteristicas(dados.get("caracteristicas") or []):
        if not exige_evidencia or caracteristica_tem_evidencia(nome, texto_norm):
            adicionar_caracteristica(caracteristicas, nome)
    for chave, nome in CARACTERISTICAS_TEXTO:
        if contem_termo_normalizado(texto_norm, chave) and _deve_adicionar_caracteristica(chave, texto_norm):
            adicionar_caracteristica(caracteristicas, nome)
    dados["caracteristicas"] = filtrar_caracteristicas(caracteristicas)
    dados["descricao_util"] = descricao_limpa
    return dados


def filtrar_caracteristicas(caracteristicas) -> list[str]:
    if isinstance(caracteristicas, str):
        caracteristicas = [p.strip() for p in re.split(r"[,;\n]+", caracteristicas) if p.strip()]
    filtradas: list[str] = []
    vistas: set[str] = set()
    for item in caracteristicas or []:
        nome = str(item).strip()
        if not nome or caracteristica_bloqueada(nome):
            continue
        chave = normalizar_caracteristica(nome)
        nome = CANONICAS.get(chave, nome)
        chave = normalizar_caracteristica(nome)
        if chave in vistas:
            continue
        vistas.add(chave)
        filtradas.append(nome)
    return filtradas
