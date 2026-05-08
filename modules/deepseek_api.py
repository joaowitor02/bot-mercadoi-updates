"""
Cliente oficial da API DeepSeek (compativel com OpenAI SDK).
Recebe a caption do Instagram e retorna JSON estruturado do imovel.
"""

import json
import re
import unicodedata
from openai import AsyncOpenAI
from modules.logger import Logger

logger = Logger("deepseek_api")

_CARACTERISTICAS_OFICIAIS = [
    "Academia", "Acesso para cadeirantes", "Área de Lazer", "Área pet", "Área Verde",
    "Banheiro social", "Biblioteca", "Bicicletário", "Campo de futebol", "Campo de Golf",
    "Churrasqueira", "Circuito de segurança", "Condomínio fechado", "Coworking",
    "Espaço gourmet", "Espaço kids", "Estacionamento para visita", "Gerador Elétrico",
    "Lavanderia", "Lounge", "Mini mercado", "Piscina adulto", "Piscina infantil",
    "Playground", "Portaria", "Portaria 24h", "Portaria eletrônica", "Quadra de areia",
    "Quadra de tênis", "Quadra poliesportiva", "Recepção", "Salão de festas / SUM",
    "Salão de jogos", "Sauna", "Segurança 24h", "Sistema de alarme", "Solarium", "Spa",
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

_SYSTEM = """Voce e um especialista em anuncios imobiliarios brasileiros.
Analise a legenda de um post do Instagram e extraia apenas informacoes realmente presentes.

REGRAS GERAIS:
- Responda APENAS com JSON valido, sem texto adicional, sem markdown e sem explicacoes.
- Nunca invente dados ausentes. Use "" para campos nao informados.
- Tudo que nao estiver especificado na legenda deve ficar vazio ou fora da lista.
- titulo: use a primeira linha/frase da publicacao que resume o principal do imovel.
- descricao_util: mantenha a descricao da publicacao o mais fiel possivel, preservando quebras de linha, mas remova nome de pessoa, Instagram, telefone/celular, WhatsApp, CRECI isolado e chamadas de contato.
- Campos numericos devem ter apenas digitos ou "". Nunca use "0" para desconhecido.
- preco: apenas digitos, sem R$, pontos ou virgulas. NUNCA use telefone, CRM, CRECI, codigo de anuncio ou numero de contato como preco.
- operacao: use exatamente "A Venda" ou "Em Aluguel".
- tipo_imovel: use termos compativeis com o Mercadoi: Apartamento, Casa, Casa de Condominio, Terreno, Sala Comercial, Apto. Cobertura, Apto. Duplex, Apto. Flat, Apto. Garden, Chacara, Fazenda, Sitio.
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
- endereco: rua e numero se mencionados.
- cep: CEP se mencionado explicitamente, no formato 00000-000 ou apenas 8 digitos.
- latitude e longitude: coordenadas somente se estiverem explicitamente presentes.
- proximidades: lugares/vizinhancas de referencia citados na publicacao.
- caracteristicas: array JSON em ordem alfabetica, com nomes EXATAMENTE como na lista abaixo.
  Inclua apenas itens claramente mencionados ou fortemente implicitos.

CARACTERISTICAS PERMITIDAS:
Academia; Acesso para cadeirantes; Area de Lazer; Area pet; Area Verde; Banheiro social; Biblioteca; Bicicletario; Campo de futebol; Campo de Golf; Churrasqueira; Circuito de seguranca; Condominio fechado; Coworking; Espaco gourmet; Espaco kids; Estacionamento para visita; Gerador Eletrico; Lavanderia; Lounge; Mini mercado; Piscina adulto; Piscina infantil; Playground; Portaria; Portaria 24h; Portaria eletronica; Quadra de areia; Quadra de tenis; Quadra poliesportiva; Recepcao; Salao de festas / SUM; Salao de jogos; Sauna; Seguranca 24h; Sistema de alarme; Solarium; Spa; Terraco/Rooftop; Vaga coberta; Vestiario; Aceita animais; Agua inclusa; Aquecedor; Aquecimento central; Ar Condicionado; Area de servico; Area externa privativa; Churrasqueira propria; Closet; Conexao a internet; Cozinha; Cozinha americana; Cozinha Gourmet; Cozinha independente; DCE - Dependencia de empregada; Deposito; Despensa; Energia solar; Entrada de servico; Escritorio; Espaco Gourmet; Freezer; Gas Central; Geladeira; Gramado / Jardim; Hidromassagem/Jacuzzi; Interfone; Jacuzzi; Lareira; Lava-louca; Lavadora de roupas; Mezanino; Microondas; Mobiliado; Piscina Privativa; Porteira Fechada; Projetados; Sala de estar; Sala em 2 ambientes; Suite; Telefone; TV; TV a cabo; Varanda; Varanda gourmet; Varanda Integrada; Ventilado.

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

            logger.info(f"Titulo: {dados.get('titulo', '')[:80]}")
            return dados

        except Exception as e:
            logger.error(f"Erro na API DeepSeek: {e}")
            return None


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


def _norm_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def _normalizar_caracteristicas(valores: list) -> list[str]:
    mapa = {_norm_texto(v): v for v in _CARACTERISTICAS_OFICIAIS}
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
    return sorted(dict.fromkeys(saida), key=lambda s: _norm_texto(s))


def _mesclar_caracteristicas(*listas: list) -> list[str]:
    saida = []
    for lista in listas:
        for valor in lista or []:
            if valor:
                saida.append(str(valor).strip())
    return sorted(dict.fromkeys(saida), key=lambda s: _norm_texto(s))


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
