"""
Scraper HTTP para anúncios de imóveis do OLX Brasil.
Extrai dados via __NEXT_DATA__ (Next.js) sem browser.
"""

import json
import re
import os
import asyncio
import html as html_lib
import unicodedata
import httpx
from modules.logger import Logger
from modules.property_types import normalizar_tipo_imovel
from modules.caracteristicas_guard import adicionar_caracteristica, enriquecer_caracteristicas_por_texto, filtrar_caracteristicas

logger = Logger("olx_scraper")

_OLX_URL_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)?olx\.com\.br(?:/|$)",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

_TIPO_MAP = {
    "apartamento": "Apartamento",
    "apartamentos": "Apartamento",
    "casa":         "Casa",
    "casas":        "Casa",
    "terreno":      "Terreno",
    "terrenos":     "Terreno",
    "lote":         "Terreno",
    "sala":         "Sala Comercial",
    "comercial":    "Sala Comercial",
    "loja":         "Sala Comercial",
    "galpao":       "Sala Comercial",
    "galpão":       "Sala Comercial",
    "flat":         "Apto. Flat",
    "cobertura":    "Apto. Cobertura",
    "garden":       "Apto. Garden",
    "studio":       "Apto. Studio",
    "kitnet":       "Apartamento",
    "sitio":        "Sítio",
    "sítio":        "Sítio",
    "chacara":      "Chácaras",
    "chácara":      "Chácaras",
    "fazenda":      "Fazenda",
}


def url_valida(url: str) -> bool:
    return bool(_OLX_URL_RE.match(url.strip()))


def normalizar_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^(?:[\w-]+\.)?olx\.com\.br(?:/|$)", url, re.IGNORECASE):
        return f"https://{url}"
    return url


def _normalizar_tipo(texto: str) -> str:
    return normalizar_tipo_imovel(texto)


def _limpar_preco(valor) -> str:
    if not valor:
        return ""
    s = str(valor)
    apenas_digitos = re.sub(r"\D", "", s)
    # Remove zeros à esquerda absurdos (ex: centavos vieram junto)
    if len(apenas_digitos) > 10:
        apenas_digitos = apenas_digitos[:10]
    return apenas_digitos or ""


def _formatar_preco(valor: str) -> str:
    digits = re.sub(r"\D", "", str(valor or ""))
    if not digits:
        return ""
    return f"R$ {int(digits):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _texto_limpo(valor) -> str:
    if not valor:
        return ""
    texto = html_lib.unescape(str(valor))
    texto = texto.replace("\\n", "\n").replace("\\r", "\n").replace("\\/", "/")
    texto = re.sub(r"<br\s*/?>", "\n", texto, flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*\n+", "\n\n", texto)
    return texto.strip()


def _norm_texto(valor: str) -> str:
    texto = unicodedata.normalize("NFD", str(valor or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _primeiro_numero(texto: str, padroes: list[str]) -> str:
    for padrao in padroes:
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            return re.sub(r"\D", "", m.group(1))
    return ""


def _primeiro_valor_monetario(texto: str, padroes: list[str]) -> str:
    for padrao in padroes:
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            return _limpar_preco(m.group(1))
    return ""


def _extrair_proximidades(texto: str) -> str:
    padroes = [
        r"(?:proximo|próximo|perto)\s+(?:a|de|do|da|dos|das)\s+([^\n.]{8,120})",
        r"(?:facil acesso|fácil acesso)\s+(?:a|para)\s+([^\n.]{8,120})",
        r"(?:comercios|comércios|servicos|serviços|escolas|farmacias|farmácias|supermercados)[^\n.]{0,120}",
    ]
    for padrao in padroes:
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            trecho = m.group(1) if m.lastindex else m.group(0)
            return re.sub(r"\s+", " ", trecho).strip(" .,-")
    return ""


def _adicionar_caracteristica(features: list, nome: str):
    adicionar_caracteristica(features, nome)


def _enriquecer_por_texto(dados: dict) -> dict:
    texto = "\n".join([
        str(dados.get("titulo", "")),
        str(dados.get("descricao_util", "")),
        " ".join(str(c) for c in (dados.get("caracteristicas") or [])),
    ])
    n = _norm_texto(texto)
    features = list(dados.get("caracteristicas") or [])

    if not dados.get("suites"):
        dados["suites"] = _primeiro_numero(texto, [
            r"(\d+)\s*su[ií]tes?",
            r"sendo\s+(\d+)\s*su[ií]tes?",
        ])
    if not dados.get("banheiros"):
        dados["banheiros"] = _primeiro_numero(texto, [r"(\d+)\s*banheiros?"])
    if not dados.get("vagas"):
        dados["vagas"] = _primeiro_numero(texto, [r"(\d+)\s*vagas?"])
    if not dados.get("condominio"):
        dados["condominio"] = _primeiro_valor_monetario(texto, [
            r"condom[ií]nio\s*(?:de|:|-)?\s*r?\$?\s*([\d\.,]+)",
            r"cond\.\s*r?\$?\s*([\d\.,]+)",
        ])
    if not dados.get("iptu"):
        dados["iptu"] = _primeiro_valor_monetario(texto, [
            r"iptu\s*(?:de|:|-)?\s*r?\$?\s*([\d\.,]+)",
        ])
    if not dados.get("andar"):
        if re.search(r"\bt[eé]rreo\b", texto, re.IGNORECASE):
            dados["andar"] = "Térreo"
        else:
            dados["andar"] = _primeiro_numero(texto, [
                r"(\d+)[ºoªa]?\s*andar",
                r"andar\s*(\d+)",
            ])
    if not dados.get("elevador"):
        if "sem elevador" in n or "nao tem elevador" in n or "nao possui elevador" in n:
            dados["elevador"] = "Não"
        elif "elevador" in n:
            dados["elevador"] = "Sim"
    if not dados.get("perto_do_mar"):
        if "frente mar" in n or "beira mar" in n or "pe na areia" in n:
            dados["perto_do_mar"] = "Frente para o mar"
        elif "quadra do mar" in n:
            dados["perto_do_mar"] = "Quadra do mar"
        elif re.search(r"\b\d+\s*(?:m|metros)\s+do\s+mar\b", texto, re.IGNORECASE) or "perto da praia" in n:
            dados["perto_do_mar"] = "Próximo ao mar"
    if not dados.get("mobiliado"):
        if "semi mobiliado" in n or "semi-mobiliado" in n:
            dados["mobiliado"] = "Semi-mobiliado"
        elif "sem mobilia" in n or "sem moveis" in n:
            dados["mobiliado"] = "Sem mobília"
        elif "mobiliado" in n or "moveis planejados" in n or "projetados" in n:
            dados["mobiliado"] = "Mobiliado"
    if not dados.get("escriturado") and ("escritura" in n or "escriturado" in n):
        dados["escriturado"] = "Sim"
    if not dados.get("aceita_financiamento"):
        if "nao aceita financiamento" in n:
            dados["aceita_financiamento"] = "Não"
        elif "aceita financiamento" in n or "financiavel" in n or "financiável" in texto.lower():
            dados["aceita_financiamento"] = "Sim"
    if not dados.get("aceita_permuta"):
        if "nao aceita permuta" in n:
            dados["aceita_permuta"] = "Não"
        elif "aceita permuta" in n:
            dados["aceita_permuta"] = "Sim"
    if not dados.get("aceita_airbnb"):
        if "nao aceita airbnb" in n or "nao aceita temporada" in n:
            dados["aceita_airbnb"] = "Não"
        elif "aceita airbnb" in n or "aceita temporada" in n or "aluguel por temporada" in n:
            dados["aceita_airbnb"] = "Sim"
    if not dados.get("estagio_imovel"):
        if re.search(r"\bem\s*constru[cç][aã]o\b|\bem\s*obras?\b", texto, re.IGNORECASE):
            dados["estagio_imovel"] = "Em Construção"
        elif re.search(r"\blan[cç]amento\b", texto, re.IGNORECASE):
            dados["estagio_imovel"] = "Lançamento"
        elif re.search(r"\bpronto\s+para\s+morar\b|\bprontas?\s+para\s+morar\b", texto, re.IGNORECASE):
            dados["estagio_imovel"] = "Pronto para morar"
        elif re.search(r"\breformad[oa]\b", texto, re.IGNORECASE):
            dados["estagio_imovel"] = "Reformado"
        elif re.search(r"\bsemi.?nov[oa]\b", texto, re.IGNORECASE):
            dados["estagio_imovel"] = "Semi Novo"
    if not dados.get("ano_construcao"):
        m = re.search(r"\b(?:ano\s+de\s+constru[cç][aã]o|constru[ií]do\s+em|entregue\s+em|entrega\s+em)[:\s]+(\d{4})\b", texto, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", texto)
        if m:
            dados["ano_construcao"] = m.group(1)
    if not dados.get("posicao_predio"):
        if re.search(r"\bfrente\b", texto, re.IGNORECASE) and not re.search(r"frente\s+(?:ao\s+mar|para\s+o\s+mar|praia)", texto, re.IGNORECASE):
            dados["posicao_predio"] = "Frente"
        elif "fundos" in n or re.search(r"\bfundo\b", texto, re.IGNORECASE):
            dados["posicao_predio"] = "Fundo"
        elif "lateral" in n:
            dados["posicao_predio"] = "Lateral"
    if not dados.get("posicao_solar"):
        for chave, valor in [
            ("nascente", "Leste"), ("poente", "Oeste"), ("nordeste", "Nordeste"),
            ("sudeste", "Sudeste"), ("sudoeste", "Sudoeste"), ("noroeste", "Noroeste"),
            ("sol da manha", "Sol da manhã"), ("sol da tarde", "Sol da tarde"),
        ]:
            if chave in n:
                dados["posicao_solar"] = valor
                break
    if not dados.get("proximidades"):
        dados["proximidades"] = _extrair_proximidades(texto)

    dados["caracteristicas"] = features
    dados = enriquecer_caracteristicas_por_texto(dados)
    dados["caracteristicas"] = filtrar_caracteristicas(dados.get("caracteristicas") or [])
    return dados


def _json_string(valor: str) -> str:
    try:
        return json.loads(f'"{valor}"')
    except Exception:
        return valor


def _prop(properties: list, *nomes: str) -> str:
    """Extrai valor de uma propriedade da lista de properties do OLX."""
    for item in properties:
        nome = (item.get("name") or item.get("label") or "").lower()
        for n in nomes:
            if n in nome:
                val = item.get("value") or item.get("values") or ""
                if isinstance(val, list):
                    val = val[0] if val else ""
                return str(val).strip()
    return ""


def _buscar_descricao_json(obj) -> str:
    if isinstance(obj, dict):
        for key in ("description", "body", "text"):
            val = obj.get(key)
            if isinstance(val, str):
                texto = _texto_limpo(val)
                if len(texto) > 40:
                    return texto
        for val in obj.values():
            texto = _buscar_descricao_json(val)
            if texto:
                return texto
    elif isinstance(obj, list):
        for item in obj:
            texto = _buscar_descricao_json(item)
            if texto:
                return texto
    return ""


def _extrair_descricao_html(html: str) -> str:
    """Extrai a descrição longa do anúncio quando ela não vem no analytics."""
    html_decoded = html_lib.unescape(html)

    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_decoded,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        texto = _buscar_descricao_json(data)
        if texto:
            return texto

    for pattern in [
        r'"description"\s*:\s*"((?:\\.|[^"\\]){40,})"',
        r'"body"\s*:\s*"((?:\\.|[^"\\]){40,})"',
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']{40,})["\']',
        r'<meta[^>]+content=["\']([^"\']{40,})["\'][^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    ]:
        m = re.search(pattern, html_decoded, flags=re.IGNORECASE | re.DOTALL)
        if m:
            texto = _texto_limpo(_json_string(m.group(1)))
            if texto:
                return texto
    return ""


def _valor_recursivo(obj, nomes: tuple[str, ...], profundidade: int = 0) -> str:
    if profundidade > 8:
        return ""
    nomes_norm = {_norm_texto(n) for n in nomes}
    if isinstance(obj, dict):
        for key, val in obj.items():
            key_norm = _norm_texto(key)
            if key_norm in nomes_norm or any(n in key_norm for n in nomes_norm):
                if isinstance(val, (str, int, float)):
                    texto = _texto_limpo(val)
                    if texto:
                        return texto
                if isinstance(val, dict):
                    for subkey in ("label", "value", "name", "text"):
                        texto = _texto_limpo(val.get(subkey, ""))
                        if texto:
                            return texto
            achado = _valor_recursivo(val, nomes, profundidade + 1)
            if achado:
                return achado
    elif isinstance(obj, list):
        for item in obj[:20]:
            achado = _valor_recursivo(item, nomes, profundidade + 1)
            if achado:
                return achado
    return ""


def _endereco_plausivel(texto: str) -> str:
    texto = _texto_limpo(texto)
    if not texto:
        return ""
    texto = re.sub(r"\s*,\s*", ", ", texto).strip(" ,.-")
    norm = _norm_texto(texto)
    if not norm or norm in {"pb", "paraiba", "brasil"}:
        return ""
    if len(texto) <= 3:
        return ""
    # Evita jogar apenas bairro/cidade/estado no campo de rua.
    if not re.search(
        r"\b(rua|avenida|av\.?|travessa|rodovia|estrada|alameda|pra[çc]a|loteamento|residencial|condom[ií]nio)\b",
        texto,
        re.IGNORECASE,
    ) and not re.search(r"\d", texto):
        return ""
    return texto


def _extrair_endereco_olx(data: dict, html: str = "") -> str:
    candidatos = [
        _valor_recursivo(data, (
            "streetAddress", "street_address", "address", "street", "logradouro",
            "addressLine", "address_line", "fullAddress", "full_address",
            "propertyAddress", "mapAddress",
        )),
    ]

    if html:
        html_decoded = html_lib.unescape(html)
        for pattern in [
            r'"streetAddress"\s*:\s*"([^"]{4,120})"',
            r'"street_address"\s*:\s*"([^"]{4,120})"',
            r'"address"\s*:\s*"([^"]{4,160})"',
            r'"logradouro"\s*:\s*"([^"]{4,120})"',
            r'<meta[^>]+(?:property|name)=["\'][^"\']*(?:street|address|logradouro)[^"\']*["\'][^>]+content=["\']([^"\']{4,160})["\']',
            r'<meta[^>]+content=["\']([^"\']{4,160})["\'][^>]+(?:property|name)=["\'][^"\']*(?:street|address|logradouro)[^"\']*["\']',
        ]:
            for m in re.finditer(pattern, html_decoded, flags=re.IGNORECASE):
                candidatos.append(_json_string(m.group(1)))

    for candidato in candidatos:
        endereco = _endereco_plausivel(candidato)
        if endereco:
            return endereco
    return ""


def _extrair_cep_olx(data: dict, html: str = "") -> str:
    # 1ª prioridade: campos estruturados JSON (mais confiáveis)
    val_json = _valor_recursivo(data, ("postalCode", "postal_code", "zipcode", "zipCode", "cep"))
    if val_json:
        m = re.search(r"\b(\d{5}[-\s]?\d{3})\b", str(val_json))
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 8:
                return f"{digits[:5]}-{digits[5:]}"

    # 2ª prioridade: CEP em contexto explícito no HTML
    texto = str(html or "")
    for pat in [
        r'"postalCode"\s*:\s*"(\d{5}-?\d{3})"',
        r'"cep"\s*:\s*"(\d{5}-?\d{3})"',
        r'\bCEP\s*:?\s*(\d{5}[-\s]?\d{3})\b',
    ]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 8:
                return f"{digits[:5]}-{digits[5:]}"

    # 3ª prioridade: qualquer padrão CEP no HTML (menos confiável)
    m = re.search(r"\b(\d{5}[-\s]?\d{3})\b", texto)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 8:
            return f"{digits[:5]}-{digits[5:]}"
    return ""


def _montar_descricao_fallback(dados: dict) -> str:
    titulo = dados.get("titulo", "").strip()
    bairro = dados.get("bairro_extraido", "").strip()
    cidade = dados.get("cidade_extraida", "").strip()
    tipo = dados.get("tipo_imovel", "Imóvel").strip() or "Imóvel"

    linhas = [titulo]
    local = " em ".join([v for v in [bairro, cidade] if v])
    if local:
        linhas.extend(["", f"{tipo} localizado em {local}."])

    detalhes = []
    if dados.get("area_m2"):
        detalhes.append(f"{dados['area_m2']}m²")
    if dados.get("quartos"):
        detalhes.append(f"{dados['quartos']} quarto(s)")
    if dados.get("suites"):
        detalhes.append(f"{dados['suites']} suíte(s)")
    if dados.get("banheiros"):
        detalhes.append(f"{dados['banheiros']} banheiro(s)")
    if dados.get("vagas"):
        detalhes.append(f"{dados['vagas']} vaga(s)")
    if detalhes:
        linhas.extend(["", "Detalhes do imóvel:", *detalhes])

    features = dados.get("caracteristicas") or []
    if features:
        linhas.extend(["", "Características:", *features[:12]])

    if dados.get("condominio"):
        linhas.extend(["", f"Condomínio: {dados['condominio']}"])
    if dados.get("preco"):
        rotulo = "Valor de aluguel" if "aluguel" in dados.get("operacao", "").lower() else "Valor de venda"
        linhas.append(f"{rotulo}: {_formatar_preco(dados['preco'])}")

    return "\n".join(linhas).strip()


def _extrair_analytics_json(html: str) -> dict | None:
    """
    Extrai o JSON analytics embutido no HTML do OLX (novo formato sem __NEXT_DATA__).
    Contém: subject, price, municipality, neighbourhood, rooms, bathrooms, etc.
    """
    # Busca objeto JSON que contenha "subject" (título do anúncio) e "listId"
    for pattern in [
        r'\{[^<>{]*"subject"\s*:\s*"[^"]+[^<>]*"listId"\s*:\s*\d+[^<>{}]*\}',
        r'\{[^<>{]*"listId"\s*:\s*\d+[^<>]*"subject"\s*:\s*"[^"]+[^<>{}]*\}',
    ]:
        m = re.search(pattern, html)
        if m:
            try:
                data = json.loads(m.group(0))
                if data.get("subject") and data.get("listId"):
                    return data
            except Exception:
                continue

    # Fallback: busca mais ampla por qualquer JSON com "subject" e "price"
    m = re.search(r'\{[^<>]*"subject"\s*:\s*"[^"]{3,}"[^<>]*\}', html)
    if m:
        try:
            data = json.loads(m.group(0))
            if data.get("subject"):
                return data
        except Exception:
            pass
    return None


def _montar_dados_analytics(data: dict, url: str, imagens_urls: list, html: str = "") -> dict:
    """Monta dados normalizados a partir do JSON analytics do OLX (novo formato)."""
    titulo = data.get("subject", "")
    preco = _limpar_preco(data.get("price", ""))

    # Operação: detecta aluguel por real_estate_type ou URL
    tipo_negocio = str(data.get("real_estate_type", "")).lower()
    url_lower = url.lower()
    eh_aluguel = "aluguel" in tipo_negocio or "rent" in tipo_negocio or "aluguel" in url_lower
    operacao = "Em Aluguel" if eh_aluguel else "A Venda"

    # Tipo de imóvel: usa subCategory ou real_estate_type
    cat = data.get("subCategory") or data.get("category") or ""
    tipo_imovel = _normalizar_tipo(cat or titulo)

    cidade     = data.get("municipality", "")
    bairro     = data.get("neighbourhood", "")
    estado     = data.get("state", "")
    endereco   = _extrair_endereco_olx(data, html)
    cep        = _extrair_cep_olx(data, html)
    quartos    = str(data.get("rooms", ""))
    banheiros  = str(data.get("bathrooms", ""))
    vagas      = str(data.get("garage_spaces", ""))
    area       = str(data.get("size", ""))
    condominio = data.get("condominio", "")

    # Características
    features = []
    for campo in ["re_features", "re_complex_features", "re_types"]:
        v = data.get(campo, "")
        if v:
            features.extend([f.strip() for f in str(v).split(",") if f.strip()])

    dados = {
        "titulo":          titulo,
        "descricao_util":  _texto_limpo(data.get("description", "")),
        "preco":           preco,
        "tipo_imovel":     tipo_imovel,
        "operacao":        operacao,
        "quartos":         quartos,
        "suites":          "",
        "banheiros":       banheiros,
        "vagas":           vagas,
        "area_m2":         area,
        "cidade_extraida": cidade,
        "bairro_extraido": bairro,
        "endereco":        endereco,
        "cep":             cep,
        "url_publicacao":  url,
        "whatsapp_url":    "",
        "instagram_url":   "",
        "estagio_imovel":  "",
        "andar":           "",
        "elevador":        "",
        "area_terreno":    "",
        "ano_construcao":  "",
        "condominio":      condominio,
        "iptu":            "",
        "taxas":           "",
        "perto_do_mar":    "",
        "posicao_solar":   "",
        "posicao_predio":  "",
        "mobiliado":       "",
        "escriturado":     "",
        "aceita_permuta":  "",
        "aceita_airbnb":   "",
        "aceita_financiamento": "",
        "proximidades":    "",
        "caracteristicas": features,
    }
    dados = _enriquecer_por_texto(dados)
    if not dados["descricao_util"] or dados["descricao_util"].lower() == titulo.lower():
        dados["descricao_util"] = _montar_descricao_fallback(dados)
    return dados, imagens_urls


def _extrair_imagens_html(html: str) -> list:
    """Extrai URLs de imagens do HTML do OLX (novo formato)."""
    html = html_lib.unescape(html)
    urls = []
    vistas = set()
    # Busca padrões de imagem OLX no HTML
    for pattern in [
        r'"original"\s*:\s*"(https://[^"]+olx[^"]+\.(?:jpg|jpeg|png|webp))"',
        r'"url"\s*:\s*"(https://[^"]+olx[^"]+\.(?:jpg|jpeg|png|webp))"',
        r'(https://img\.olx\.com\.br/(?:images|thumbs\d+x\d+)/[^\s"\'<>,}]+?\.(?:jpg|jpeg|png|webp))',
        r'(https://[^"\'<>,}\s]+olx[^"\'<>,}\s]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<>,}\s]*)?)',
    ]:
        encontrados = re.findall(pattern, html)
        for u in encontrados:
            chave = re.sub(r"\?.*$", "", u)
            m_id = re.search(r"/(\d+)\.(?:jpg|jpeg|png|webp)$", chave, re.IGNORECASE)
            chave = m_id.group(1) if m_id else chave
            if chave not in vistas and "no-logo" not in u and "static" not in u:
                urls.append(u)
                vistas.add(chave)
    return urls[:10]


def _extrair_next_data(html: str) -> dict | None:
    # Tenta padrões diferentes do script tag (OLX pode variar)
    for pattern in [
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        r'<script\s+id=["\']__NEXT_DATA__["\'][^>]*>(\{.*?\})</script>',
        r'id=["\']__NEXT_DATA__["\'][^>]*>(\{[^<]+)<',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return None


def _busca_recursiva_ad(obj, profundidade: int = 0) -> dict | None:
    """Busca recursiva por objeto de anúncio no JSON do OLX."""
    if profundidade > 8:
        return None
    if isinstance(obj, dict):
        # Heurística: objeto de anúncio OLX sempre tem subject/title + body/description
        tem_titulo = obj.get("subject") or obj.get("title")
        tem_corpo = "body" in obj or "description" in obj or "params" in obj
        if tem_titulo and tem_corpo and len(obj) > 3:
            return obj
        for v in obj.values():
            result = _busca_recursiva_ad(v, profundidade + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj[:5]:  # limita busca em listas longas
            result = _busca_recursiva_ad(item, profundidade + 1)
            if result:
                return result
    return None


def _parse_ad(data: dict) -> dict | None:
    """Navega pela estrutura do __NEXT_DATA__ e retorna o objeto do anúncio."""
    pp = data.get("props", {}).get("pageProps", {})

    # Tenta caminhos conhecidos (ordem de probabilidade)
    for caminho in [
        lambda d: d.get("ad"),
        lambda d: d.get("adData", {}).get("ad"),
        lambda d: d.get("initialProps", {}).get("ad"),
        lambda d: d.get("data", {}).get("ad"),
        lambda d: d.get("listing"),
        lambda d: d.get("adDetail"),
        lambda d: d.get("pageData", {}).get("ad"),
        lambda d: d.get("serverData", {}).get("ad"),
        lambda d: d.get("props", {}).get("ad"),
    ]:
        try:
            ad = caminho(pp)
            if ad and isinstance(ad, dict) and (ad.get("subject") or ad.get("title")):
                return ad
        except Exception:
            continue

    # Fallback: busca recursiva em todo o pageProps
    ad = _busca_recursiva_ad(pp)
    if ad:
        logger.info("OLX: objeto de anúncio encontrado via busca recursiva")
        return ad

    # Log diagnóstico para facilitar correções futuras
    chaves = list(pp.keys()) if isinstance(pp, dict) else type(pp).__name__
    logger.warning(f"OLX __NEXT_DATA__ estrutura desconhecida. Chaves em pageProps: {chaves}")
    return None


def _str(val) -> str:
    """Extrai string de valor que pode ser dict, list ou primitivo."""
    if isinstance(val, dict):
        return str(val.get("label") or val.get("value") or val.get("name") or "").strip()
    if isinstance(val, list):
        return str(val[0]).strip() if val else ""
    return str(val).strip() if val else ""


def _montar_dados(ad: dict, url: str) -> dict:
    props = ad.get("properties") or ad.get("params") or ad.get("features") or []

    titulo   = ad.get("subject") or ad.get("title") or ad.get("name") or ""
    descricao = _texto_limpo(ad.get("body") or ad.get("description") or ad.get("text") or "")

    # Preço — aceita dict ou valor direto
    price_field = ad.get("price") or ad.get("priceValue") or {}
    if isinstance(price_field, dict):
        preco_raw = (
            price_field.get("value")
            or price_field.get("price")
            or price_field.get("amount")
            or ""
        )
    else:
        preco_raw = price_field
    preco = _limpar_preco(preco_raw)

    # Operação — detecta aluguel por múltiplos campos
    tipo_negocio = (
        _str(ad.get("type"))
        or _str(ad.get("adType"))
        or _str(ad.get("businessType"))
        or _str(ad.get("transactionType"))
        or ad.get("listingType", "")
    )
    # Também verifica na URL
    url_lower = url.lower()
    eh_aluguel = (
        "aluguel" in tipo_negocio.lower()
        or "rent" in tipo_negocio.lower()
        or "aluguel" in url_lower
        or "/alugar/" in url_lower
    )
    operacao = "Em Aluguel" if eh_aluguel else "A Venda"

    # Tipo de imóvel — combina categoria + subcategoria + título
    cat = (
        _str(ad.get("category"))
        or _str(ad.get("categoryName"))
        or _str(ad.get("categoryLabel"))
        or _str(ad.get("subcategory"))
        or ""
    )
    tipo_imovel = _normalizar_tipo(cat or titulo)

    # Localização — tenta location, address e campos de topo
    loc = ad.get("location") or ad.get("address") or {}
    if not isinstance(loc, dict):
        loc = {}
    cidade = (
        loc.get("municipality") or loc.get("city") or
        loc.get("municipalityLabel") or loc.get("municipalityCode") or
        ad.get("municipality") or ad.get("city") or ""
    )
    bairro = (
        loc.get("neighbourhood") or loc.get("district") or
        loc.get("neighbourhoodLabel") or loc.get("zone") or
        ad.get("neighbourhood") or ad.get("district") or ""
    )
    endereco = loc.get("address") or loc.get("street") or ad.get("street") or ""
    cep = _extrair_cep_olx(ad)

    # Características numéricas
    quartos  = _prop(props, "room", "quarto", "bedroom", "dormit")
    suites   = _prop(props, "suite")
    banheiros = _prop(props, "bathroom", "banheiro")
    vagas    = _prop(props, "garage", "vaga", "parking")
    area     = _prop(props, "square_meter", "area", "m2", "tamanho", "useful_area")

    # Corrige suites > quartos
    try:
        if int(suites) > int(quartos):
            suites = str(int(quartos) // 2) if int(quartos) > 1 else ""
    except Exception:
        pass

    # Imagens
    imgs_raw = ad.get("images") or ad.get("photos") or []
    imagens_urls = []
    for img in imgs_raw:
        if isinstance(img, dict):
            u = img.get("original") or img.get("url") or img.get("src") or ""
        elif isinstance(img, str):
            u = img
        else:
            continue
        if u:
            imagens_urls.append(u)

    # Telefone → WhatsApp (se disponível)
    phone = ad.get("phone") or (ad.get("user") or {}).get("phone") or ""
    wa = ""
    if phone:
        digits = re.sub(r"\D", "", str(phone))
        if digits:
            wa = f"https://wa.me/55{digits}"

    dados = {
        "titulo":          titulo,
        "descricao_util":  descricao,
        "preco":           preco,
        "tipo_imovel":     tipo_imovel,
        "operacao":        operacao,
        "quartos":         quartos,
        "suites":          suites,
        "banheiros":       banheiros,
        "vagas":           vagas,
        "area_m2":         area,
        "cidade_extraida": cidade,
        "bairro_extraido": bairro,
        "endereco":        endereco,
        "cep":             cep,
        "url_publicacao":  url,
        "whatsapp_url":    wa,
        "instagram_url":   "",
        "estagio_imovel":  "",
        "andar":           "",
        "elevador":        "",
        "area_terreno":    "",
        "ano_construcao":  "",
        "condominio":      "",
        "iptu":            "",
        "taxas":           "",
        "perto_do_mar":    "",
        "posicao_solar":   "",
        "posicao_predio":  "",
        "mobiliado":       "",
        "escriturado":     "",
        "aceita_permuta":  "",
        "aceita_airbnb":   "",
        "aceita_financiamento": "",
        "proximidades":    "",
        "caracteristicas": [],
    }
    dados = _enriquecer_por_texto(dados)
    if not dados["descricao_util"] or dados["descricao_util"].lower() == titulo.lower():
        dados["descricao_util"] = _montar_descricao_fallback(dados)
    return dados, imagens_urls


class OlxScraper:
    def __init__(self, zenrows_key: str = "", scraperapi_key: str = "", worker_url: str = ""):
        self._zenrows_key = zenrows_key
        self._scraperapi_key = scraperapi_key
        self._worker_url = worker_url.rstrip("/")

    async def extrair(self, url: str) -> dict:
        """
        Extrai dados de um anúncio OLX.
        Ordem: httpx → ZenRows/ScraperAPI (se configurado) → Playwright stealth
        Retorna:
          ok=True  → {ok, dados, imagens_urls}
          ok=False → {ok, motivo}
        """
        url = normalizar_url(url)
        if not url_valida(url):
            return {"ok": False, "motivo": "url_invalida"}

        # Tentativa 1: httpx direto (rápido)
        html = None
        if self._worker_url:
            logger.info("OLX: tentando Cloudflare Worker...")
            html = await self._fetch_worker(url)
        if html in (None, "403"):
            html = await self._fetch_httpx(url)
        if html == "404":
            return {"ok": False, "motivo": "nao_encontrado"}

        # Tentativa 2: Cloudflare Worker (grátis, bypassa Cloudflare pela própria rede deles)
        if False and html in (None, "403") and self._worker_url:
            logger.info("OLX bloqueado — tentando Cloudflare Worker...")
            html = await self._fetch_worker(url)

        # Tentativa 3: curl_cffi — imita fingerprint TLS do Chrome
        if html in (None, "403"):
            logger.info("OLX bloqueado — tentando curl_cffi...")
            html = await self._fetch_curl_cffi(url)

        # Tentativa 4: API de scraping paga (se configurada)
        if html in (None, "403"):
            if self._zenrows_key:
                logger.info("OLX bloqueado — tentando ZenRows...")
                html = await self._fetch_zenrows(url)
            elif self._scraperapi_key:
                logger.info("OLX bloqueado — tentando ScraperAPI...")
                html = await self._fetch_scraperapi(url)

        # Tentativa 5: Playwright com stealth
        if html in (None, "403"):
            logger.info("OLX bloqueado — tentando Playwright stealth...")
            html = await self._fetch_playwright(url)

        if not html:
            return {"ok": False, "motivo": "acesso_restrito"}

        # Tenta novo formato (JSON analytics, sem __NEXT_DATA__)
        analytics = _extrair_analytics_json(html)
        if analytics:
            if not analytics.get("description"):
                descricao_html = _extrair_descricao_html(html)
                if descricao_html:
                    analytics["description"] = descricao_html
            imagens_urls = _extrair_imagens_html(html)
            dados, imagens_urls = _montar_dados_analytics(analytics, url, imagens_urls, html)
            if dados.get("titulo"):
                logger.info(
                    f"OLX extraído (analytics): '{dados['titulo'][:60]}' | "
                    f"{dados['cidade_extraida']} | R${dados['preco']} | "
                    f"{len(imagens_urls)} imagem(ns)"
                )
                return {"ok": True, "dados": dados, "imagens_urls": imagens_urls}

        # Tenta formato antigo (__NEXT_DATA__)
        next_data = _extrair_next_data(html)
        if next_data:
            ad = _parse_ad(next_data)
            if ad:
                dados, imagens_urls = _montar_dados(ad, url)
                if dados.get("titulo"):
                    logger.info(
                        f"OLX extraído (__NEXT_DATA__): '{dados['titulo'][:60]}' | "
                        f"{dados['cidade_extraida']} | R${dados['preco']} | "
                        f"{len(imagens_urls)} imagem(ns)"
                    )
                    return {"ok": True, "dados": dados, "imagens_urls": imagens_urls}

        logger.warning(f"OLX: nenhum formato reconhecido em {url}")
        return {"ok": False, "motivo": "estrutura_desconhecida"}

    async def _fetch_httpx(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers=_HEADERS,
            ) as client:
                r = await client.get(url)
            if r.status_code == 404:
                return "404"
            if r.status_code in (403, 429):
                return "403"
            if r.status_code != 200:
                return None
            return r.text
        except Exception as e:
            logger.warning(f"OLX httpx falhou: {e}")
            return None

    async def _fetch_playwright(self, url: str) -> str | None:
        try:
            from playwright.async_api import async_playwright
            try:
                from playwright_stealth import stealth_async
                _stealth = stealth_async
            except ImportError:
                _stealth = None

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--window-size=1920,1080",
                    ],
                )
                context = await browser.new_context(
                    user_agent=_HEADERS["User-Agent"],
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                        "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                    },
                )
                page = await context.new_page()
                # Remove propriedades que denunciam automação
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR','pt','en-US','en']});
                    window.chrome = {runtime: {}};
                """)
                if _stealth:
                    await _stealth(page)

                await page.goto(url, timeout=45000, wait_until="domcontentloaded")

                # Detecta Cloudflare challenge e aguarda resolução
                for _ in range(10):
                    title = await page.title()
                    if "attention required" in title.lower() or "just a moment" in title.lower():
                        logger.info("OLX: aguardando Cloudflare challenge...")
                        await page.wait_for_timeout(3000)
                    else:
                        break

                try:
                    await page.wait_for_function(
                        "document.getElementById('__NEXT_DATA__') !== null",
                        timeout=15000,
                    )
                except Exception:
                    pass

                html = await page.content()
                await browser.close()

                # Verifica se ainda está na página de challenge
                if "attention required" in html.lower()[:2000] and "__NEXT_DATA__" not in html:
                    logger.warning("OLX: Cloudflare não resolvido mesmo com stealth")
                    return None

                return html
        except Exception as e:
            logger.warning(f"OLX Playwright falhou: {e}")
            return None

    async def _fetch_worker(self, url: str) -> str | None:
        """Busca OLX via Cloudflare Worker (bypassa bloqueio de IP de data center)."""
        try:
            from urllib.parse import quote
            worker_url = f"{self._worker_url}?url={quote(url, safe='')}"
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(worker_url)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
            logger.warning(f"Cloudflare Worker retornou status {r.status_code}")
            return None
        except Exception as e:
            logger.warning(f"OLX Worker falhou: {e}")
            return None

    async def _fetch_curl_cffi(self, url: str) -> str | None:
        """Imita fingerprint TLS/HTTP2 do Chrome real — bypassa Cloudflare sem API key."""
        try:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession() as session:
                r = await session.get(
                    url,
                    impersonate="chrome120",
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Cache-Control": "no-cache",
                    },
                    timeout=30,
                )
            if r.status_code == 404:
                return "404"
            if r.status_code in (403, 429):
                return "403"
            if r.status_code == 200:
                return r.text
            return None
        except ImportError:
            logger.warning("curl_cffi não instalado — rode: pip install curl-cffi")
            return None
        except Exception as e:
            logger.warning(f"OLX curl_cffi falhou: {e}")
            return None

    async def _fetch_zenrows(self, url: str) -> str | None:
        """Bypass Cloudflare via ZenRows (proxy residencial + JS render)."""
        try:
            from urllib.parse import quote
            params = {
                "url": url,
                "apikey": self._zenrows_key,
                "js_render": "true",
                "premium_proxy": "true",
                "proxy_country": "br",
            }
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get("https://api.zenrows.com/v1/", params=params)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
            logger.warning(f"ZenRows retornou status {r.status_code}")
            return None
        except Exception as e:
            logger.warning(f"OLX ZenRows falhou: {e}")
            return None

    async def _fetch_scraperapi(self, url: str) -> str | None:
        """Bypass Cloudflare via ScraperAPI (proxy residencial + JS render)."""
        try:
            from urllib.parse import quote
            params = {
                "api_key": self._scraperapi_key,
                "url": url,
                "render": "true",
                "country_code": "br",
            }
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get("https://api.scraperapi.com/", params=params)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
            logger.warning(f"ScraperAPI retornou status {r.status_code}")
            return None
        except Exception as e:
            logger.warning(f"OLX ScraperAPI falhou: {e}")
            return None

    async def baixar_imagens(self, imagens_urls: list, downloads_path: str) -> list[str]:
        """Baixa imagens do OLX diretamente (sem Apify)."""
        os.makedirs(downloads_path, exist_ok=True)
        async def _baixar_uma(client, i: int, url_img: str) -> str | None:
            try:
                r = await client.get(url_img, headers={"Referer": "https://www.olx.com.br/"})
                if r.status_code != 200:
                    return None
                ext = ".jpg"
                ct = r.headers.get("content-type", "")
                if "png" in ct:
                    ext = ".png"
                caminho = os.path.join(downloads_path, f"olx_{i+1}{ext}")
                with open(caminho, "wb") as f:
                    f.write(r.content)
                logger.info(f"Imagem OLX {i+1}/{len(imagens_urls)} baixada")
                return caminho
            except Exception as e:
                logger.warning(f"Erro ao baixar imagem OLX {i+1}: {e}")
                return None

        arquivos = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            tasks = [_baixar_uma(client, i, url_img) for i, url_img in enumerate(imagens_urls[:10])]
            resultados = await asyncio.gather(*tasks, return_exceptions=True)
            for r in resultados:
                if isinstance(r, Exception):
                    logger.warning(f"Erro ao baixar imagem OLX: {r}")
                elif r:
                    arquivos.append(r)
            return arquivos
            for i, url_img in enumerate(imagens_urls[:10]):  # máx 10 imagens
                try:
                    r = await client.get(url_img, headers={"Referer": "https://www.olx.com.br/"})
                    if r.status_code != 200:
                        continue
                    ext = ".jpg"
                    ct = r.headers.get("content-type", "")
                    if "png" in ct:
                        ext = ".png"
                    caminho = os.path.join(downloads_path, f"olx_{i+1}{ext}")
                    with open(caminho, "wb") as f:
                        f.write(r.content)
                    arquivos.append(caminho)
                    logger.info(f"Imagem OLX {i+1}/{len(imagens_urls)} baixada")
                except Exception as e:
                    logger.warning(f"Erro ao baixar imagem OLX {i+1}: {e}")
        return arquivos
