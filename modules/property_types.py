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
    texto = _normalizar_texto(" ".join(str(p or "") for p in partes))
    tipos = []
    tem_casa = bool(re.search(r"\b(casa|casas|sobrado|sobrados)\b", texto))
    tem_condominio = "condominio" in texto or "condominium" in texto

    if tem_casa and tem_condominio:
        return ["Casa de Condom\u00ednio"]
    if tem_casa:
        return ["Casa"]
    if "cobertura" in texto:
        tipos.append("Apto. Cobertura")
    if "duplex" in texto:
        tipos.append("Apto. Duplex")
    if tipos:
        return tipos

    if "flat" in texto:
        return ["Apto. Flat"]
    if "garden" in texto:
        return ["Apto. Garden"]
    if "studio" in texto or "apart" in texto or "apto" in texto or "kitnet" in texto or "kit net" in texto:
        return ["Apartamento"]
    if "chacara" in texto:
        return ["Ch\u00e1cara"]
    if "fazenda" in texto:
        return ["Fazenda"]
    if "sitio" in texto:
        return ["S\u00edtio"]
    if re.search(r"\bresidencia(s)?\b", texto):
        return ["Casa"]
    if "terreno" in texto or "lote" in texto:
        return ["Terreno"]
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
