"""
Cliente oficial da API DeepSeek (compativel com OpenAI SDK).
Recebe a caption do Instagram e retorna JSON estruturado do imovel.
"""

import json
import re
import unicodedata
from openai import AsyncOpenAI
from modules.logger import Logger
from modules.caracteristicas_guard import filtrar_caracteristicas

logger = Logger("deepseek_api")

_CARACTERISTICAS_OFICIAIS = [
    "Academia", "Acesso para cadeirantes", "Área de Lazer", "Área pet", "Área Verde",
    "Banheiro social", "Biblioteca", "Bicicletário", "Campo de futebol", "Campo de Golf",
    "Churrasqueira", "Circuito de segurança", "Condomínio fechado", "Coworking",
    "Espaço gourmet", "Espaço kids", "Estacionamento para visita", "Gerador Elétrico",
    "Lavanderia", "Lounge", "Mini mercado", "Piscina adulto", "Piscina infantil",
    "Playground", "Portaria", "Portaria 24h", "Portaria eletrônica", "Quadra de areia",
    "Quadra de tênis", "Quadra poliesportiva", "Recepção", "Salão de festas / SUM",
    "Sala de jogos", "Sauna", "Segurança 24h", "Sistema de alarme", "Solarium", "Spa",
    "Terraço/Rooftop", "Vaga coberta", "Vestiário", "Aceita animais", "Agua inclusa",
    "Aquecedor", "Aquecimento central", "Ar Condicionado", "Área de serviço",
    "Área externa privativa", "Churrasqueira propria", "Closet", "Conexão à internet",
    "Cozinha", "Cozinha americana", "Cozinha Gourmet", "Cozinha independente",
    "DCE - Dependência de empregada", "Depósito", "Despensa", "Energia solar",
    "Entrada de serviço", "Escritório", "Espaço Gourmet", "Freezer", "Gás Central",
    "Geladeira", "Gramado / Jardim", "Hidromassagem/Jacuzzi", "Interfone", "Jacuzzi",
    "Lareira", "Lava-louça", "Lavadora de roupas", "Mezanino", "Microondas",
    "Mobiliado", "Piscina Privativa", "Porteira Fechada", "Projetados", "Sala de estar",
    "Sala em 2 ambientes", "Suíte", "Telefone", "TV", "TV a cabo", "Varanda",
    "Varanda gourmet", "Varanda Integrada", "Ventilado",
]

_CARACTERISTICAS_PERMITIDAS = "; ".join(filtrar_caracteristicas(_CARACTERISTICAS_OFICIAIS))

_SYSTEM = f"""Voce e um especialista em anuncios imobiliarios brasileiros.
Analise a legenda de um post do Instagram e extraia apenas informacoes realmente presentes.

REGRAS GERAIS:
- Responda APENAS com JSON valido, sem texto adicional, sem markdown e sem explicacoes.
- Nunca invente dados ausentes. Use "" para campos nao informados.
- Tudo que nao estiver especificado na legenda deve ficar vazio ou fora da lista.
- titulo: gere um titulo descritivo no estilo imobiliario, como nos exemplos: "Apartamento 2 quartos no Tambau", "Casa 4 quartos em Manaira - Joao Pessoa", "Cobertura 120m² em Cabo Branco", "Apartamento Studio no Bessa". Use: tipo de imovel + quartos ou area + bairro ou cidade. Se faltar dados, use nome do empreendimento ou caracteristica principal (ex: "Pé na Areia - Jardim Oceania"). NUNCA use frases de marketing, chamadas de acao ou exclamacoes como titulo (ex: NAO use "Chegou sua chance!", "Oportunidade imperdivel", "Sair do aluguel", "Grande lancamento").
- descricao_util: mantenha a descricao da publicacao o mais fiel possivel, preservando quebras de linha, mas remova nome de pessoa, Instagram, telefone/celular, WhatsApp, CRECI isolado e chamadas de contato.
- Campos numericos devem ter apenas digitos ou "". Nunca use "0" para desconhecido.
- preco: apenas digitos, sem R$, pontos ou virgulas. NUNCA use telefone, CRM, CRECI, codigo de anuncio ou numero de contato como preco.
- operacao: use exatamente "A Venda" ou "Em Aluguel".
- tipo_imovel: use termos compativeis com o Mercadoi: Apartamento, Casa, Casa de Condominio, Terreno, Sala Comercial, Apto. Cobertura, Apto. Duplex, Apto. Flat, Apto. Garden, Apto. Studio, Chacaras, Fazenda, Sitio.
  Chacara/minichacara sempre vira Chacaras, mesmo quando a descricao mencionar casa sede.
  Casa duplex/sobrado continua sendo Casa. Casa em condominio vira Casa de Condominio. Terreno/lote vira Terreno.
  So use Apto. Cobertura quando a cobertura for o tipo da unidade; area de lazer na cobertura do predio nao muda o tipo.
- estagio_imovel: breve lancamento, lancamento, em construcao, novo, seminovo, usado ou reformado.
- elevador, escriturado, aceita_airbnb e aceita_financiamento: use "Sim", "Nao" ou "".
- aceita_permuta: use "Sim", "Nao" ou "".
- mobiliado: use "sem mobilia", "semi mobiliado", "Mobiliado" ou "Mobiliado e Decorado".
- perto_do_mar: use "Vista para o mar", "Frente para o mar", "Quadra do mar", "Proximo ao mar" ou "".
- posicao_predio: use "Frente", "Fundo", "Lateral", "Meio" ou "".
- condominio, iptu e taxas: valores em reais, apenas digitos.
- area_m2 e area_terreno: area em m2, apenas digitos. Para apartamentos, area_terreno geralmente fica "".
- bairro_extraido: nome do bairro onde o imovel esta localizado. Extraia sempre que o bairro aparecer — mesmo que indiretamente, como "em Manaira", "no Tambau", "CABO BRANCO -", "Bessa, Joao Pessoa". Use apenas o nome do bairro, sem cidade ou estado.
- cidade_extraida: cidade do imovel no formato "Cidade/UF" (ex: "Joao Pessoa/PB"). Se nao houver UF, use apenas o nome da cidade.
- endereco: rua e numero se mencionados.
- cep: CEP se mencionado explicitamente, no formato 00000-000 ou apenas 8 digitos.
- latitude e longitude: coordenadas somente se estiverem explicitamente presentes.
- proximidades: lugares/vizinhancas de referencia citados na publicacao.
- caracteristicas: array JSON em ordem alfabetica, com nomes EXATAMENTE como na lista abaixo.
  Inclua apenas itens claramente mencionados. Nao use itens que nao aparecam na legenda.

CARACTERISTICAS PERMITIDAS:
{_CARACTERISTICAS_PERMITIDAS}

REGRAS CRITICAS:
- quartos: apenas dormitorios/quartos. Nao incluir pavimentos, andares ou comodos.
- suites: apenas quartos com banheiro proprio. Nunca maior que quartos.
- banheiros: banheiros completos + lavabos, validando valores absurdos.
- vagas: vagas de garagem. Nao confundir com pavimentos/andares.
- Se houver duvida entre telefone e preco, deixe preco vazio."""

_USER_TEMPLATE = """URL da publicacao: {url_publicacao}
Perfil do Instagram: {perfil_instagram}

Legenda:
{caption}

Retorne exatamente este JSON preenchido:
{{
  "titulo": "",
  "descricao_util": "",
  "tipo_imovel": "",
  "operacao": "",
  "preco": "",
  "estagio_imovel": "",
  "andar": "",
  "elevador": "",
  "quartos": "",
  "suites": "",
  "banheiros": "",
  "vagas": "",
  "area_m2": "",
  "area_terreno": "",
  "ano_construcao": "",
  "condominio": "",
  "iptu": "",
  "taxas": "",
  "endereco": "",
  "cep": "",
  "latitude": "",
  "longitude": "",
  "posicao_solar": "",
  "mobiliado": "",
  "escriturado": "",
  "perto_do_mar": "",
  "aceita_permuta": "",
  "posicao_predio": "",
  "aceita_airbnb": "",
  "aceita_financiamento": "",
  "proximidades": "",
  "cidade_extraida": "",
  "bairro_extraido": "",
  "url_publicacao": "",
  "whatsapp_url": "",
  "instagram_url": "",
  "caracteristicas": []
}}"""


class DeepSeekAPIClient:
    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )

    async def extrair(
        self,
        caption: str,
        url_publicacao: str,
        perfil_instagram: str = "",
    ) -> dict | None:
        """
        Envia caption para a API e retorna dict com os campos do imovel.
        Retorna None em caso de falha.
        """
        logger.info("Enviando para API DeepSeek...")
        prompt = _USER_TEMPLATE.format(
            url_publicacao=url_publicacao,
            perfil_instagram=perfil_instagram or "",
            caption=caption or "(legenda nao disponivel)",
        )
        try:
            resp = await self._client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            logger.info(f"Resposta API recebida ({len(content)} chars)")

            dados = _parse_json(content)
            if not dados:
                return None

            if not dados.get("url_publicacao"):
                dados["url_publicacao"] = url_publicacao
            if not dados.get("instagram_url") and perfil_instagram:
                dados["instagram_url"] = perfil_instagram
            whatsapp_detectado = _extrair_whatsapp_url(
                "\n".join([
                    str(dados.get("whatsapp_url") or ""),
                    str(caption or ""),
                ])
            )
            if whatsapp_detectado:
                dados["whatsapp_url"] = whatsapp_detectado
            if isinstance(dados.get("descricao_util"), str):
                dados["descricao_util"] = _limpar_descricao(dados["descricao_util"])
            if isinstance(dados.get("caracteristicas"), list):
                dados["caracteristicas"] = _normalizar_caracteristicas(dados["caracteristicas"])
            else:
                dados["caracteristicas"] = []
            if _texto_tem_academia("\n".join([
                str(dados.get("titulo") or ""),
                str(dados.get("descricao_util") or ""),
                str(caption or ""),
            ])):
                dados["caracteristicas"] = _mesclar_caracteristicas(
                    dados.get("caracteristicas") or [],
                    ["Academia"],
                )

            # Reescreve título de marketing pela versão descritiva
            titulo_atual = dados.get("titulo", "")
            if _titulo_e_marketing(titulo_atual):
                titulo_novo = _gerar_titulo_descritivo(dados, str(caption or ""))
                if titulo_novo:
                    logger.info(f"Título de marketing substituído: '{titulo_atual[:60]}' → '{titulo_novo}'")
                    dados["titulo"] = titulo_novo

            logger.info(f"Titulo: {dados.get('titulo', '')[:80]}")
            return dados

        except Exception as e:
            logger.error(f"Erro na API DeepSeek: {e}")
            return None


_RE_MARKETING = re.compile(
    r'\b(chegou|oportunidade|nao perca|nao perde|aproveite|venha|confira|conquiste'
    r'|sua chance|seu sonho|seu lar|sair do aluguel|realizando sonhos|novo lar'
    r'|casa propria|imperdivel|perfeito para|garanta|descubra|conheca|sonho de'
    r'|nao fique|quer sair|voce pode|sua familia|mude de vida|vida nova'
    r'|realize seu|construindo sonhos|transforme|exclusivo para|grande lancamento'
    r'|lancamento imperdivel|esperava para|sempre sonharam|sempre sonhou)\b',
    re.IGNORECASE,
)


def _norm_simples(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


def _titulo_e_marketing(titulo: str) -> bool:
    if not titulo:
        return True
    t = _norm_simples(titulo)
    if _RE_MARKETING.search(t):
        return True
    tem_info = bool(re.search(
        r'\b(apto|apartamento|casa|cobertura|terreno|studio|kitnet|flat|duplex|garden'
        r'|quarto|suite|m2|bairro|jardim|praia|rua|avenida)\b', t
    ))
    return not tem_info and len(titulo.split()) <= 5


def _gerar_titulo_descritivo(dados: dict, caption: str = "") -> str:
    tipo = str(dados.get("tipo_imovel") or "").strip()
    quartos = str(dados.get("quartos") or "").strip()
    bairro = str(dados.get("bairro_extraido") or "").strip()
    cidade = str((dados.get("cidade_extraida") or "").split("/")[0]).strip()
    area = str(dados.get("area_m2") or "").strip()
    operacao = str(dados.get("operacao") or "").strip()
    mobiliado = str(dados.get("mobiliado") or "").strip().lower()

    partes = []
    if tipo:
        partes.append(tipo)
    if quartos and quartos not in ("0", ""):
        partes.append(f"{quartos} quarto{'s' if quartos != '1' else ''}")
    elif area and area not in ("0", ""):
        partes.append(f"{area}m²")

    local = bairro or cidade
    if local:
        fem = bool(re.search(
            r'(beira|praia|ilha|avenida|area|rua|mangabeira|valentina|torre|penha)$',
            local, re.IGNORECASE,
        ))
        partes.append(("na" if fem else "no") + " " + local)
        if cidade and bairro and cidade.lower() not in bairro.lower():
            partes.append(cidade)

    if mobiliado in ("mobiliado", "mobiliado e decorado"):
        partes.append("Mobiliado")
    if operacao.lower() == "em aluguel":
        partes.append("– Aluguel")

    if len(partes) >= 2:
        return " ".join(partes)

    # Última tentativa: primeira linha descritiva da legenda
    for linha in (caption or "").split("\n"):
        linha = re.sub(r'[\U00010000-\U0010ffff]', '', linha).strip(' "\'')
        if len(linha) > 10 and not _RE_MARKETING.search(linha):
            return linha.split(".")[0].strip()

    return " ".join(partes) if partes else ""


def _parse_json(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    logger.error(f"Parse JSON falhou: {content[:300]}")
    return None


def _limpar_descricao(texto: str) -> str:
    texto = re.sub(r"https?://\S+", "", texto or "")
    texto = re.sub(r"wa\.me/\S+", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"@\w+", "", texto)
    linhas = []
    for linha in texto.splitlines():
        l = linha.strip()
        if not l:
            linhas.append("")
            continue
        if re.search(r"(instagram|whatsapp|telefone|celular|contato)\s*:?", l, re.IGNORECASE):
            continue
        if re.search(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}", l):
            continue
        linhas.append(linha.rstrip())
    return "\n".join(linhas).strip()


def _extrair_whatsapp_url(texto: str) -> str:
    texto = str(texto or "")
    if not texto.strip():
        return ""

    m = re.search(r"(?:https?://)?(?:wa\.me|api\.whatsapp\.com/send\?phone=)/?(\d{12,13})", texto, re.IGNORECASE)
    if m:
        return f"https://wa.me/{m.group(1)}"

    for m in re.finditer(
        r"(?<!\d)(?:\+?55[\s().-]*)?(?:\(?([1-9]{2})\)?[\s().-]*)?(9?[\s().-]*\d{4}[\s().-]*\d{4})(?!\d)",
        texto,
    ):
        bruto = m.group(0)
        digitos = re.sub(r"\D", "", bruto)
        if len(digitos) in (10, 11):
            digitos = "55" + digitos
        if digitos.startswith("55") and len(digitos) in (12, 13):
            return f"https://wa.me/{digitos}"

    return ""


def _norm_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def _normalizar_caracteristicas(valores: list) -> list[str]:
    mapa = {_norm_texto(v): v for v in filtrar_caracteristicas(_CARACTERISTICAS_OFICIAIS)}
    saida = []
    for valor in valores:
        chave = _norm_texto(valor)
        if not chave:
            continue
        oficial = mapa.get(chave)
        if not oficial:
            for k, v in mapa.items():
                if chave == k or chave in k or k in chave:
                    oficial = v
                    break
        if oficial:
            saida.append(oficial)
    return sorted(dict.fromkeys(filtrar_caracteristicas(saida)), key=lambda s: _norm_texto(s))


def _mesclar_caracteristicas(*listas: list) -> list[str]:
    saida = []
    for lista in listas:
        for valor in lista or []:
            if valor:
                saida.append(str(valor).strip())
    return sorted(dict.fromkeys(filtrar_caracteristicas(saida)), key=lambda s: _norm_texto(s))


def _texto_tem_academia(texto: str) -> bool:
    alvo = _norm_texto(texto)
    termos = (
        "academia",
        "fitness",
        "espaco fitness",
        "sala de ginastica",
        "musculacao",
        "gym",
    )
    return any(t in alvo for t in termos)
