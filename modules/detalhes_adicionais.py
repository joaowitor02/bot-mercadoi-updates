"""
Extrai detalhes livres para o bloco "Detalhes adicionais" do Mercadoi.

Usado para informacoes especificas que valorizam o imovel, mas que nao sao
checkboxes confiaveis de caracteristicas/areas comuns.
"""

from __future__ import annotations

from modules.caracteristicas_guard import (
    caracteristica_bloqueada,
    contem_termo_normalizado,
    normalizar_caracteristica,
)


DETALHES_PADROES: list[tuple[str, tuple[str, ...]]] = [
    # Cozinha: nao captura "cozinha" isolado.
    ("Cozinha americana", ("cozinha americana",)),
    ("Cozinha gourmet", ("cozinha gourmet",)),
    ("Cozinha reservada", ("cozinha reservada",)),
    ("Cozinha planejada", ("cozinha planejada", "cozinha projetada", "cozinha com planejados")),
    ("Cozinha integrada", ("cozinha integrada", "cozinha servico integrados", "cozinha e servico integrados")),
    ("Cozinha com ilha", ("cozinha com ilha",)),
    ("Cozinha com bancada", ("cozinha com bancada", "bancada em granito", "bancada de granito", "bancada em porcelanato")),

    # Area de servico/lavanderia privativa.
    ("Área de serviço", ("area de servico",)),
    ("Área de serviço separada", ("area de servico separada",)),
    ("Área de serviço integrada", ("area de servico integrada", "servico integrado", "servico integrados")),
    ("Área de serviço ventilada", ("area de servico ventilada",)),
    ("Lavanderia privativa", ("lavanderia privativa", "lavanderia propria")),

    # Salas.
    ("Sala ampla", ("sala ampla",)),
    ("Sala de jantar", ("sala de jantar",)),
    ("Sala de estar", ("sala de estar",)),
    ("Sala de vídeo", ("sala de video",)),
    ("Sala em L", ("sala em l", "sala de jantar em l", "sala de video em l")),
    ("Sala integrada", ("sala integrada", "sala estar jantar integrada", "sala de estar jantar integrada")),
    ("Sala para dois ambientes", ("sala para dois ambientes", "sala dois ambientes", "sala em 2 ambientes")),
    ("Sala de estar/jantar", ("sala de estar jantar", "estar jantar")),

    # Varandas.
    ("Varanda", ("varanda",)),
    ("Varanda gourmet", ("varanda gourmet",)),
    ("Varanda integrada", ("varanda integrada",)),
    ("Varanda ampla", ("varanda ampla",)),
    ("Varanda nascente", ("varanda nascente",)),
    ("Varanda com vista", ("varanda com vista",)),
    ("Varanda privativa", ("varanda privativa",)),

    # Quintal / area externa privativa.
    ("Quintal amplo", ("quintal amplo", "quinta amplo")),
    ("Quintal em L", ("quintal em l", "quinta em l")),
    ("Área externa privativa", ("area externa privativa",)),
    ("Jardim privativo", ("jardim privativo",)),
    ("Espaço externo", ("espaco externo",)),

    # Moveis e acabamentos.
    ("Móveis projetados", ("moveis projetados", "moveis planejados", "armarios planejados")),
    ("Projetados", ("projetados",)),
    ("Projetados na cozinha", ("projetados na cozinha", "cozinha com projetados")),
    ("Projetados na sala", ("projetados na sala", "sala com projetados")),
    ("Projetados nos quartos", ("projetados nos quartos", "quartos com projetados")),
    ("Projetados na área de serviço", ("projetados na area de servico", "area de servico com projetados")),
    ("Bancada em granito", ("bancada em granito", "bancada de granito")),
    ("Bancada em porcelanato", ("bancada em porcelanato",)),
    ("Piso em porcelanato", ("piso em porcelanato", "porcelanato")),
    ("Acabamento alto padrão", ("acabamento alto padrao", "alto padrao")),

    # Conforto / posicao.
    ("Andar alto", ("andar alto",)),
    ("Ventilado", ("ventilado", "ventilacao natural")),
    ("Nascente", ("nascente",)),
    ("Sombra", ("sombra",)),
    ("Vista livre", ("vista livre",)),
    ("Vista para o mar", ("vista para o mar", "vista mar")),
    ("Frente para rua", ("frente para rua",)),

    # Dependencias e apoio.
    ("Casa sede", ("casa sede",)),
    ("Casa de apoio", ("casa de apoio",)),
    ("Dependência completa", ("dependencia completa", "dce completa")),
    ("DCE", ("dce", "dependencia de empregada")),
    ("Despensa", ("despensa",)),
    ("Depósito", ("deposito",)),
    ("Home office", ("home office",)),
    ("Escritório", ("escritorio",)),
    ("Closet", ("closet",)),
    ("Lavabo", ("lavabo",)),
    ("Redário", ("redario",)),

    # Imoveis rurais / terrenos.
    ("Topografia plana", ("topografia plana", "topografia todo plano", "todo plano", "terreno plano")),
    ("Energia trifásica", ("energia trifasica",)),
    ("Escritura pública", ("escritura publica",)),
    ("Galpão", ("galpao",)),
    ("Cocheira", ("cocheira", "cocheiras")),
    ("Curral", ("curral", "currais")),
    ("Propriedade toda cercada", ("propriedade toda cercada", "toda cercada", "todo cercado", "propriedade cercada")),
    ("Divisórias internas", ("divisorias internas", "divisoria interna", "divisoes internas")),
    ("Tanque", ("tanque", "tanque na pedra")),
    ("Tanque na pedra", ("tanque na pedra",)),

    # Vagas com qualificadores.
    ("Vaga coberta", ("vaga coberta", "vagas cobertas", "vaga de garagem coberta", "vagas de garagem cobertas", "garagem coberta")),
    ("Vaga privativa", ("vaga privativa", "vagas privativas")),
    ("Vagas soltas", ("vagas soltas", "vaga solta")),
    ("Vagas paralelas", ("vagas paralelas", "vaga paralela")),
]


CHECKBOX_DISPONIVEIS = {
    normalizar_caracteristica(x)
    for x in [
        "Academia",
        "Área de Lazer",
        "Área pet / Espaço pet",
        "Bicicletário",
        "Brinquedoteca",
        "Churrasqueira",
        "Coworking",
        "Elevador",
        "Espaço gourmet",
        "Espaço kids",
        "Espaço kids / Brinquedoteca",
        "Hidromassagem",
        "Lavanderia",
        "Lounge",
        "Mini mercado",
        "Piscina adulto",
        "Playground",
        "Portaria",
        "Quadra de areia",
        "Quadra poliesportiva",
        "Sala de jogos",
        "Salão de festas",
        "Segurança",
        "Solarium",
        "Vaga coberta",
    ]
}


def _como_lista(detalhes) -> list[dict[str, str]]:
    normalizados: list[dict[str, str]] = []
    if isinstance(detalhes, dict):
        detalhes = [{"titulo": k, "valor": v} for k, v in detalhes.items()]
    for item in detalhes or []:
        if isinstance(item, dict):
            titulo = str(item.get("titulo") or item.get("title") or item.get("nome") or "").strip()
            valor = str(item.get("valor") or item.get("value") or item.get("detalhe") or "").strip()
        elif isinstance(item, (list, tuple)) and item:
            titulo = str(item[0] or "").strip()
            valor = str(item[1] if len(item) > 1 else "").strip()
        else:
            titulo = str(item or "").strip()
            valor = ""
        if titulo and not caracteristica_bloqueada(titulo):
            normalizados.append({"titulo": titulo, "valor": valor or "Sim"})
    return normalizados


def normalizar_detalhes_adicionais(detalhes) -> list[dict[str, str]]:
    saida: list[dict[str, str]] = []
    vistos: set[str] = set()
    for item in _como_lista(detalhes):
        chave = normalizar_caracteristica(item["titulo"])
        if not chave or chave in vistos or _ja_eh_checkbox(item["titulo"]):
            continue
        vistos.add(chave)
        saida.append(item)
    return saida


def _remover_sobreposicoes(detalhes: list[dict[str, str]]) -> list[dict[str, str]]:
    titulos = {normalizar_caracteristica(item["titulo"]) for item in detalhes}
    remover = set()
    if {"bancada em granito", "cozinha com bancada"} <= titulos:
        remover.add("cozinha com bancada")
    if {"bancada em porcelanato", "cozinha com bancada"} <= titulos:
        remover.add("cozinha com bancada")
    if "sala de estar jantar" in titulos:
        remover.update({"sala de estar", "sala de jantar"})
    if any(t.startswith("varanda ") for t in titulos):
        remover.add("varanda")
    if "moveis projetados" in titulos or any(t.startswith("projetados ") for t in titulos):
        remover.add("projetados")
    if any(t.startswith("area de servico ") for t in titulos):
        remover.add("area de servico")
    if "tanque na pedra" in titulos:
        remover.add("tanque")
    return [
        item for item in detalhes
        if normalizar_caracteristica(item["titulo"]) not in remover
    ]


def _texto_evidencia(dados: dict) -> str:
    return normalizar_caracteristica("\n".join([
        str(dados.get("titulo") or ""),
        str(dados.get("descricao_util") or ""),
    ]))


def _ja_eh_checkbox(titulo: str) -> bool:
    return normalizar_caracteristica(titulo) in CHECKBOX_DISPONIVEIS


def enriquecer_detalhes_adicionais(dados: dict) -> dict:
    dados = dict(dados or {})
    texto_norm = _texto_evidencia(dados)
    detalhes = normalizar_detalhes_adicionais(dados.get("detalhes_adicionais") or [])
    vistos = {normalizar_caracteristica(item["titulo"]) for item in detalhes}

    for titulo, termos in DETALHES_PADROES:
        chave = normalizar_caracteristica(titulo)
        if chave in vistos or _ja_eh_checkbox(titulo) or caracteristica_bloqueada(titulo):
            continue
        for termo in termos:
            termo_norm = normalizar_caracteristica(termo)
            if termo_norm and contem_termo_normalizado(texto_norm, termo_norm):
                detalhes.append({"titulo": titulo, "valor": "Sim"})
                vistos.add(chave)
                break

    dados["detalhes_adicionais"] = _remover_sobreposicoes(detalhes)
    return dados


def detalhes_para_meta(detalhes) -> list[dict[str, str]]:
    return [
        {
            "fave_additional_feature_title": item["titulo"],
            "fave_additional_feature_value": item["valor"] or "Sim",
        }
        for item in normalizar_detalhes_adicionais(detalhes)
    ]


def detalhes_para_post_fields(detalhes) -> dict[str, str]:
    campos: dict[str, str] = {}
    for idx, item in enumerate(normalizar_detalhes_adicionais(detalhes)):
        campos[f"additional_features[{idx}][fave_additional_feature_title]"] = item["titulo"]
        campos[f"additional_features[{idx}][fave_additional_feature_value]"] = item["valor"] or "Sim"
    return campos
