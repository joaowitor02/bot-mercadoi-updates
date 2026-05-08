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
    "Spa",
    "Terraco",
    "Terraco Rooftop",
    "Rooftop",
    "Sistema de alarme",
    "Piscina infantil",
    "Piscina Privativa",
]}


CARACTERISTICAS_TEXTO = [
    ("ar condicionado", "Ar Condicionado"),
    ("varanda gourmet", "Varanda gourmet"),
    ("varanda", "Varanda"),
    ("area de servico", "Área de serviço"),
    ("cozinha americana", "Cozinha americana"),
    ("cozinha gourmet", "Cozinha Gourmet"),
    # Espaço gourmet, Piscina adulto, Academia, Lounge, Salão de festas e Rooftop
    # são bloqueados pelo mercadoi_driver._NUNCA_MARCAR — não faz sentido adicioná-los aqui.
    ("moveis planejados", "Projetados"),
    ("projetados", "Projetados"),
    ("hidromassagem", "Hidromassagem"),
    ("churrasqueira", "Churrasqueira"),
    ("espaco grill", "Churrasqueira"),
    ("vaga coberta", "Vaga coberta"),
    ("vagas cobertas", "Vaga coberta"),
    ("vaga de garagem coberta", "Vaga coberta"),
    ("vagas de garagem cobertas", "Vaga coberta"),
    ("garagem coberta", "Vaga coberta"),
    ("playground", "Playground"),
    ("portaria", "Portaria"),
    ("mini mercado", "Mini mercado"),
    ("minimercado", "Mini mercado"),
    ("mini market", "Mini mercado"),
    ("lavanderia", "Lavanderia"),
    ("coworking", "Coworking"),
    ("brinquedoteca", "Brinquedoteca"),
    ("seguranca", "Segurança"),
    ("sala de jogos", "Sala de jogos"),
    ("salao de jogos", "Sala de jogos"),
]


def caracteristica_bloqueada(caracteristica: str) -> bool:
    cn = normalizar_caracteristica(caracteristica)
    return any(re.search(rf"(^| ){re.escape(b)}($| )", cn) for b in NUNCA_MARCAR)


def _tem_termo_bloqueado(texto: str) -> bool:
    tn = normalizar_caracteristica(texto)
    return any(re.search(rf"(^| ){re.escape(b)}($| )", tn) for b in NUNCA_MARCAR)


def contem_termo_normalizado(texto_norm: str, termo_norm: str) -> bool:
    return bool(re.search(rf"(^| ){re.escape(termo_norm)}($| )", texto_norm))


def limpar_descricao_bloqueada(descricao: str) -> str:
    """Não altera a descrição — preserva o texto original do anunciante."""
    return str(descricao or "").strip()


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
    texto_norm = normalizar_caracteristica("\n".join([
        str(dados.get("titulo") or ""),
        descricao_original,
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
    dados["descricao_util"] = limpar_descricao_bloqueada(descricao_original)
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
        if chave in vistas:
            continue
        vistas.add(chave)
        filtradas.append(nome)
    return filtradas
