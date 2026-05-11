"""Normalizacao de tipos de imovel para o Mercadoi."""

import html
import re
import unicodedata


def _normalizar_texto(texto: str) -> str:
    texto = html.unescape(str(texto or "")).lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_tipos_imovel(*partes: str) -> list[str]:
    partes_norm = [_normalizar_texto(p) for p in partes if str(p or "").strip()]
    texto = _normalizar_texto(" ".join(partes_norm))
    texto_principal = _normalizar_texto(" ".join(partes_norm[:2]))
    texto_tipo = partes_norm[0] if partes_norm else ""
    tipos = []
    texto_sem_casa_rural = re.sub(r"\bcasa\s+(sede|de\s+apoio)\b", " ", texto)
    tem_casa = bool(re.search(r"\b(casa|casas|sobrado|sobrados)\b", texto_sem_casa_rural))
    tem_condominio = "condominio" in texto or "condominium" in texto
    if "chacara" in texto:
        return ["Chácaras"]
    if "sitio" in texto:
        return ["Sítio"]
    if "fazenda" in texto:
        return ["Fazenda"]
    sinais_rurais = (
        "curral", "currais", "cocheira", "cocheiras", "hectare", "hectares",
        "pecuaria", "agricultura", "gado", "pasto", "pastagem", "baia", "baias",
        "propriedade toda cercada", "toda cercada", "todo cercado",
        "divisorias internas", "casa sede", "casa de apoio",
        "zona rural", "area rural", "imovel rural",
    )
    sinais_rurais_fortes = sum(1 for termo in sinais_rurais if termo in texto)
    tem_contexto_rural = (
        sinais_rurais_fortes >= 2
        and not re.search(r"\b(apart|apto|apartamento|studio|kitnet|sala comercial|loja)\b", texto_principal)
    )

    # "terreno" no título/tipo (primeiras partes) = sinal forte de que É um terreno
    tem_terreno_principal = bool(re.search(r"\b(terreno|terrenos|lote|lotes)\b", texto_principal))

    # "terreno" apenas na descrição como medida de área (ex: "Área do Terreno: 300m²")
    # NÃO deve classificar o imóvel como Terreno — a menos que esteja no título
    tem_terreno_area = bool(re.search(
        r"\b(area|metros|m2|m²|total|fracao)\s*(de\s*)?(terreno|terrenos)\b"
        r"|\bterreno\s*(total|de|com|:)\b"
        r"|\barea\s+do\s+terreno\b",
        texto,
    ))
    tem_terreno = (
        bool(re.search(r"\b(terreno|terrenos|lote|lotes)\b", texto))
        and (tem_terreno_principal or not tem_terreno_area)  # título prevalece
        and not tem_casa
        and not bool(re.search(r"\b(apart|apto|apartamento|cobertura|flat|studio|kitnet)\b", texto_principal))
    )
    if tem_terreno:
        return ["Terreno"]

    if tem_casa and tem_condominio:
        return ["Casa de Condom\u00ednio"]
    if tem_casa:
        return ["Casa"]
    if tem_contexto_rural:
        return ["Sítio"]

    mencao_cobertura_area = bool(re.search(
        r"\b(area de lazer|lazer|rooftop|terraco|piscina|salao|espaco)\b.{0,50}\bcobertura\b"
        r"|\bcobertura\b.{0,50}\b(predio|empreendimento|coletiva|comum|lazer)\b",
        texto,
    ))
    cobertura_no_tipo = any(
        bool(re.search(r"\b(cobertura|coberturas|apartamento cobertura|apto cobertura|cobertura duplex)\b", p))
        for p in partes_norm[:2]
    )
    cobertura_tipo_forte = any(
        bool(re.search(
            r"^(apartamento|apto\.?|apt)?\s*cobertura(\s+duplex)?$"
            r"|^cobertura(\s+duplex)?$"
            r"|^duplex\s+cobertura$"
            r"|\b(apartamento|apto\.?|apt)\s+cobertura\b",
            p,
        ))
        for p in partes_norm[:2]
    )
    if cobertura_no_tipo and (cobertura_tipo_forte or not mencao_cobertura_area):
        tipos.append("Apto. Cobertura")
    if "duplex" in texto_principal:
        tipos.append("Apto. Duplex")
    if tipos:
        return tipos

    subtipos = []
    for escopo_idx, escopo in enumerate((texto_tipo, texto_principal, texto)):
        encontrados = []
        for termo, nome in (
            ("flat", "Apto. Flat"),
            ("garden", "Apto. Garden"),
            ("studio", "Apto. Studio"),
        ):
            pos = escopo.find(termo)
            if pos >= 0:
                encontrados.append((pos, nome))
        if encontrados:
            for _, nome in sorted(encontrados):
                if nome not in subtipos:
                    subtipos.append(nome)
            break
    if subtipos:
        return subtipos
    if "apart" in texto or "apto" in texto or "kitnet" in texto or "kit net" in texto:
        return ["Apartamento"]
    if re.search(r"\bresidencia(s)?\b", texto):
        return ["Casa"]
    if "sala" in texto or "comercial" in texto or "loja" in texto or "escritorio" in texto:
        return ["Sala Comercial"]
    return ["Apartamento"]


def normalizar_tipo_imovel(*partes: str) -> str:
    return normalizar_tipos_imovel(*partes)[0]


def aplicar_tipos_imovel(dados: dict) -> dict:
    """Preenche tipo_imovel e tipo_imovel_lista a partir de titulo/descricao/tipo."""
    if not dados:
        return dados

    lista_atual = dados.get("tipo_imovel_lista") or []
    if isinstance(lista_atual, str):
        lista_atual = [p.strip() for p in lista_atual.split(",") if p.strip()]

    if lista_atual:
        tipos = []
        contexto = " ".join([
            str(dados.get("tipo_imovel", "") or ""),
            str(dados.get("titulo", "") or ""),
            str(dados.get("descricao_util", "") or ""),
        ])
        for item in lista_atual:
            for tipo in normalizar_tipos_imovel(item, contexto):
                if tipo not in tipos:
                    tipos.append(tipo)
    else:
        tipos = normalizar_tipos_imovel(
            dados.get("tipo_imovel", ""),
            dados.get("titulo", ""),
            dados.get("descricao_util", ""),
        )

    dados["tipo_imovel_lista"] = tipos
    dados["tipo_imovel"] = tipos[0] if tipos else "Apartamento"
    return dados
