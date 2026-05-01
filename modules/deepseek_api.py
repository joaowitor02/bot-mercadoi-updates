"""
Cliente oficial da API DeepSeek (compatível com OpenAI SDK).
Recebe a caption do Instagram e retorna JSON estruturado do imóvel.
"""

import json
import re
from openai import AsyncOpenAI
from modules.logger import Logger

logger = Logger("deepseek_api")

_SYSTEM = """Você é um especialista em anúncios imobiliários brasileiros.
Analise a legenda de um post do Instagram sobre um imóvel e extraia os dados estruturados.

REGRAS GERAIS:
- Responda APENAS com JSON válido, sem texto adicional, sem markdown, sem blocos de código.
- Nunca invente dados ausentes. Use string vazia "" para campos não informados.
- Campos numéricos (quartos, suites, banheiros, vagas, area_m2) devem ter apenas dígitos ou "". Se o valor for zero ou desconhecido use "" — nunca use "0".
- preco: apenas dígitos, sem R$, pontos ou vírgulas. Ex: "450000". Se não informado use "". NUNCA use número de telefone, CRM ou CRECI como preço.
- operacao: use exatamente "A Venda" ou "Em Aluguel".
- tipo_imovel: ex: "Apartamento", "Casa", "Terreno", "Sala Comercial".
- elevador: use "Sim", "Não" ou "".
- estagio_imovel: "Novo", "Usado" ou "Em Construção".
- whatsapp_url: formato https://wa.me/+55...
- titulo: atraente com tipo + quartos + bairro + cidade quando disponíveis.
- descricao_util: texto comercial organizado com emojis e tópicos separados por newline.
- area_terreno: área do terreno/lote em m², apenas dígitos. Para apartamentos sempre "". Para casas e terrenos use quando informado.
- ano_construcao: ano de construção com 4 dígitos (ex: "2019") ou "".
- condominio: valor do condomínio mensal em reais, apenas dígitos, sem R$. Ex: "850". Use "" se não informado.
- endereco: rua e número se mencionados na legenda (ex: "Rua das Flores, 123"). Use "" se não informado.
- caracteristicas: array JSON com nomes de características EXATAMENTE como na lista abaixo.
  Inclua APENAS itens claramente mencionados ou fortemente implícitos. Não invente.
  Áreas Comuns: Academia, Acesso para cadeirantes, Área de Lazer, Área pet, Área Verde,
  Biblioteca, Bicicletário, Campo de futebol, Campo de Golf, Churrasqueira,
  Circuito de segurança, Condomínio fechado, Espaço gourmet, Espaço kids,
  Estacionamento para visita, Gerador Elétrico, Lavanderia, Lounge, Mini mercado,
  Piscina adulto, Piscina infantil, Playground, Portaria, Portaria 24h, Portaria eletrônica,
  Quadra de tênis, Quadra poliesportiva, Recepção, Salão de festas / SUM, Salão de jogos,
  Sauna, Segurança 24h, Sistema de alarme, Solarium, Spa, Terraço/Rooftop,
  Vaga coberta, Vestiário.
  Áreas Privativas: Aceita animais, Agua inclusa, Aquecedor, Aquecimento central,
  Ar Condicionado, Área de serviço, Área externa privativa, Churrasqueira propria, Closet,
  Conexão à internet, Cozinha, Cozinha americana, Cozinha Gourmet, Cozinha independente,
  DCE - Dependência de empregada, Depósito, Despensa, Energia solar, Entrada de serviço,
  Escritório, Espaço Gourmet, Freezer, Gás Central, Geladeira, Gramado / Jardim,
  Hidromassagem, Interfone, Jacuzzi, Lareira, Lava-louça, Lavadora de roupas, Lavanderia,
  Mezanino, Microondas, Mobiliado, Piscina, Porteira Fechada, Projetados, Sala de jantar,
  Sala em 2 ambientes, Suíte, Telefone, TV, TV a cabo, Varanda, Varanda gourmet,
  Varanda Integrada, Ventilado.

REGRAS CRÍTICAS PARA CAMPOS NUMÉRICOS:
- quartos: apenas dormitórios/quartos. NÃO incluir pavimentos, andares ou outros cômodos.
- suites: apenas quartos com banheiro próprio (suíte master, suíte). NUNCA maior que quartos.
  Se o imóvel tem 4 quartos e 2 são suítes → quartos=4, suites=2.
  Pavimentos (andares) NÃO são suítes.
- banheiros: banheiros completos + lavabos. Para casas com 3-4 quartos o máximo razoável é 5-6.
  Se a legenda mencionar "banheiro por andar" multiplique por andares, mas valide o resultado.
- vagas: vagas de garagem. Não confundir com número de pisos ou andares."""

_USER_TEMPLATE = """URL da publicação: {url_publicacao}
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
  "endereco": "",
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
        Envia caption para a API e retorna dict com os campos do imóvel.
        Retorna None em caso de falha.
        """
        logger.info("Enviando para API DeepSeek...")
        prompt = _USER_TEMPLATE.format(
            url_publicacao=url_publicacao,
            perfil_instagram=perfil_instagram or "",
            caption=caption or "(legenda não disponível)",
        )
        try:
            resp = await self._client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            logger.info(f"Resposta API recebida ({len(content)} chars)")

            dados = _parse_json(content)
            if not dados:
                return None

            # Garantir que URL e perfil não fiquem vazios se a API não preencheu
            if not dados.get("url_publicacao"):
                dados["url_publicacao"] = url_publicacao
            if not dados.get("instagram_url") and perfil_instagram:
                dados["instagram_url"] = perfil_instagram

            logger.info(f"Título: {dados.get('titulo', '')[:80]}")
            return dados

        except Exception as e:
            logger.error(f"Erro na API DeepSeek: {e}")
            return None


def _parse_json(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    logger.error(f"Parse JSON falhou: {content[:300]}")
    return None
