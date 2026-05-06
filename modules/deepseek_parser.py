"""
Modulo parser da resposta do DeepSeek.
Extrai campos estruturados do texto e tambem faz extracao inteligente
quando os dados estao dentro da descricao.
"""

import re
import unicodedata
from modules.logger import Logger
from modules.property_types import aplicar_tipos_imovel

logger = Logger("deepseek_parser")

ROTULOS = {
    "titulo": r"T[íi]tulo\s*:",
    "url_publicacao": r"Url\s+da\s+publica[çc][aã]o\s*:",
    "whatsapp_url": r"Telefone\s+ou\s+WhatsApp\s*:",
    "instagram_url": r"Usu[aá]rio\s+de?\s+Instagram\s+da\s+publica[çc][aã]o\s*:",
    "tipo_imovel": r"Tipo\s+de\s+im[oó]vel\s*:",
    "operacao": r"Tipo\s+de\s+opera[çc][aã]o\s*:|Opera[çc][aã]o\s*:",
    "preco": r"Pre[çc]o\s*:",
    "caracteristicas": r"Caracter[íi]sticas\s*:",
    "estagio_imovel": r"Est[aá]gio\s+do\s+im[oó]vel\s*:",
    "andar": r"[EÉ]\s+t[eé]rreo\s+ou\s+qual\s+andar\??\s*:|Andar\s*:",
    "elevador": r"Tem\s+Elevador\??\s*:",
    "quartos": r"Quantos\s+Quartos\??\s*:|Quartos\s*:",
    "suites": r"Quantas\s+Su[íi]tes?\s*:|Su[íi]tes?\s*:",
    "banheiros": r"Banheiros\s*:",
    "vagas": r"Vagas?\s*(?:\(garagem\))?\s*:",
    "area_m2": r"[AÁ]rea\/Metros\s+quadrados\s*(?:\(m2\))?\s*:|Tamanho\s*:|[AÁ]rea\s+constru[íi]da\s*:",
    "area_terreno": r"[AÁ]rea\s+(?:do\s+)?[Tt]erreno\s*:|[AÁ]rea\s+[Tt]otal\s+do\s+[Ll]ote\s*:",
    "ano_construcao": r"Ano\s+de\s+[Cc]onstru[çc][aã]o\s*:|Ano\s*:",
    "condominio": r"[Cc]ondom[íi]nio\s*:|[Tt]axa\s+de\s+[Cc]ondom[íi]nio\s*:",
    "iptu": r"IPTU\s*:",
    "taxas": r"Taxas?\s*:",
    "endereco": r"[Ee]ndere[çc]o\s*:|[Rr]ua\s*:",
    "cep": r"CEP\s*:|C[oÃ³]digo\s+postal\s*:",
    "latitude": r"Latitude\s*:|Lat\s*:",
    "longitude": r"Longitude\s*:|Lng\s*:|Lon\s*:",
    "posicao_solar": r"Posi[çc][aã]o\s+[Ss]olar\s*:",
    "perto_do_mar": r"Perto\s+do\s+mar\??\s*:",
    "posicao_predio": r"Posi[çc][aã]o\s+no\s+Pr[eé]dio\s*:",
    "mobiliado": r"Mobiliado\??\s*:",
    "escriturado": r"Escriturado\??\s*:",
    "aceita_permuta": r"Aceita\s+Permuta\??\s*:",
    "aceita_airbnb": r"Aceita\s+Airbnb\/Temporada\??\s*:|Aceita\s+Temporada\??\s*:",
    "aceita_financiamento": r"Aceita\s+Financiamento\??\s*:",
    "proximidades": r"Proximidades\s*:",
    "cidade_extraida": r"Cidade\s*:",
    "bairro_extraido": r"Bairro\s*:",
}

VALORES_VAZIOS = {"0", "não informado", "nao informado", "campo vazio", "-", "n/a", "não há", "nao ha", ""}

# Mapeamento keyword → nome oficial da característica (busca no texto livre)
# Usado como fallback quando o DeepSeek não retorna JSON estruturado (modo browser)
_FEATURES_KEYWORDS: list[tuple[list[str], str]] = [
    # Áreas Comuns
    (["academia", "musculação", "musculacao", "fitness"],        "Academia"),
    (["cadeirante", "acessibil"],                                "Acesso para cadeirantes"),
    (["área de lazer", "area de lazer", "lazer coletivo"],       "Área de Lazer"),
    (["área pet", "area pet", "pet friendly", "aceita pet"],     "Área pet"),
    (["área verde", "area verde", "jardim coletivo"],            "Área Verde"),
    (["banheiro social"],                                         "Banheiro social"),
    (["biblioteca"],                                             "Biblioteca"),
    (["bicicletário", "bicicletario", "bike"],                   "Bicicletário"),
    (["campo de futebol", "quadra de futebol"],                  "Campo de futebol"),
    (["campo de golf", "campo de golfe"],                        "Campo de Golf"),
    (["churrasqueira coletiva", "churrasco coletivo"],           "Churrasqueira"),
    (["circuito de segurança", "câmeras", "cameras", "cftv"],    "Circuito de segurança"),
    (["condomínio fechado", "condominio fechado"],               "Condomínio fechado"),
    (["coworking", "co-working"],                                "Coworking"),
    (["espaço kids", "espaco kids", "brinquedoteca"],            "Espaço kids"),
    (["estacionamento para visita", "vaga para visita"],         "Estacionamento para visita"),
    (["gerador"],                                                "Gerador Elétrico"),
    (["lounge"],                                                 "Lounge"),
    (["mini mercado", "minimercado"],                            "Mini mercado"),
    (["piscina adulto"],                                         "Piscina adulto"),
    (["piscina infantil"],                                       "Piscina infantil"),
    (["playground"],                                             "Playground"),
    (["portaria 24h", "portaria 24 h", "portaria vinte"],        "Portaria 24h"),
    (["portaria eletrônica", "portaria eletronica"],             "Portaria eletrônica"),
    (["portaria"],                                               "Portaria"),
    (["quadra de tênis", "quadra de tenis"],                     "Quadra de tênis"),
    (["quadra de areia", "beach tennis"],                         "Quadra de areia"),
    (["quadra poliesportiva", "quadra esportiva"],               "Quadra poliesportiva"),
    (["recepção", "recepcao"],                                   "Recepção"),
    (["salão de festas", "salao de festas", "sum"],              "Salão de festas / SUM"),
    (["salão de jogos", "salao de jogos", "game room"],          "Salão de jogos"),
    (["sauna"],                                                  "Sauna"),
    (["segurança 24h", "seguranca 24h", "vigilância 24"],        "Segurança 24h"),
    (["sistema de alarme", "alarme"],                            "Sistema de alarme"),
    (["solarium", "solário", "solario"],                         "Solarium"),
    (["spa"],                                                    "Spa"),
    (["terraço/rooftop", "rooftop", "terraço coletivo"],        "Terraço/Rooftop"),
    (["vaga coberta", "garagem coberta"],                        "Vaga coberta"),
    (["vestiário", "vestiario"],                                 "Vestiário"),
    # Áreas Privativas
    (["aceita animais", "aceita cachorro", "aceita pet"],        "Aceita animais"),
    (["água inclusa", "agua inclusa", "água incluída"],          "Agua inclusa"),
    (["aquecedor"],                                              "Aquecedor"),
    (["aquecimento central"],                                    "Aquecimento central"),
    (["ar condicionado", "ar-condicionado", "climatizado"],      "Ar Condicionado"),
    (["área de serviço", "area de servico"],                     "Área de serviço"),
    (["área externa privativa", "quintal privativo"],            "Área externa privativa"),
    (["churrasqueira própria", "churrasqueira propria"],         "Churrasqueira propria"),
    (["closet"],                                                 "Closet"),
    (["conexão à internet", "wi-fi", "wifi", "internet"],        "Conexão à internet"),
    (["cozinha gourmet"],                                        "Cozinha Gourmet"),
    (["cozinha americana"],                                      "Cozinha americana"),
    (["cozinha independente"],                                   "Cozinha independente"),
    (["cozinha"],                                                "Cozinha"),
    (["dependência de empregada", "dependencia empregada", "dce"],"DCE - Dependência de empregada"),
    (["depósito", "deposito"],                                   "Depósito"),
    (["despensa"],                                               "Despensa"),
    (["energia solar", "painel solar"],                          "Energia solar"),
    (["entrada de serviço", "entrada de servico"],               "Entrada de serviço"),
    (["escritório", "escritorio", "home office"],                "Escritório"),
    (["espaço gourmet", "espaco gourmet"],                       "Espaço Gourmet"),
    (["freezer"],                                                "Freezer"),
    (["gás central", "gas central"],                             "Gás Central"),
    (["geladeira"],                                              "Geladeira"),
    (["gramado", "jardim privativo"],                            "Gramado / Jardim"),
    (["hidromassagem", "hidromassagem/jacuzzi"],                 "Hidromassagem/Jacuzzi"),
    (["interfone", "porteiro eletrônico"],                       "Interfone"),
    (["jacuzzi", "banheira de hidromassagem"],                   "Jacuzzi"),
    (["lareira"],                                                "Lareira"),
    (["lava-louça", "lava louça", "lava-loca"],                  "Lava-louça"),
    (["lavadora de roupas", "máquina de lavar"],                 "Lavadora de roupas"),
    (["lavanderia privativa", "lavanderia própria"],             "Lavanderia"),
    (["mezanino"],                                               "Mezanino"),
    (["microondas"],                                             "Microondas"),
    (["mobiliado", "semi-mobiliado", "semi mobiliado"],          "Mobiliado"),
    (["piscina privativa", "piscina própria"],                   "Piscina Privativa"),
    (["porteira fechada", "portão automático"],                  "Porteira Fechada"),
    (["projetados", "armários projetados"],                      "Projetados"),
    (["sala de estar"],                                          "Sala de estar"),
    (["sala em 2 ambientes", "sala integrada"],                  "Sala em 2 ambientes"),
    (["suíte", "suite"],                                         "Suíte"),
    (["telefone"],                                               "Telefone"),
    (["tv a cabo"],                                              "TV a cabo"),
    (["tv"],                                                     "TV"),
    (["varanda gourmet"],                                        "Varanda gourmet"),
    (["varanda integrada"],                                      "Varanda Integrada"),
    (["varanda"],                                                "Varanda"),
    (["ventilado", "ventilação"],                                "Ventilado"),
]


class DeepSeekParser:
    def parse(self, texto):
        if not texto or not texto.strip():
            logger.error("Texto vazio recebido para parse")
            return None

        dados = {}

        # Extrair descricao como bloco entre "Descrição:" e o próximo rótulo
        descricao_bruta = self._extrair_bloco_descricao(texto)
        descricao_bruta = self._limpar_texto_estranho(descricao_bruta)
        dados["descricao_util"] = descricao_bruta

        # Extrair campos estruturados (incluindo titulo)
        linhas = texto.split("\n")
        for chave, padrao in ROTULOS.items():
            valor = self._extrair_valor(linhas, padrao)
            dados[chave] = valor

        # Fallback de titulo: monta estruturado ou usa primeira linha
        if not dados.get("titulo"):
            dados["titulo"] = self._gerar_titulo(descricao_bruta, dados)

        # Fallback: extrair dados da descricao quando nao vieram nos rotulos
        if not dados.get("quartos"):
            dados["quartos"] = self._extrair_numero_descricao(descricao_bruta, r"(\d+)\s*quarto")
        if not dados.get("suites"):
            dados["suites"] = self._extrair_numero_descricao(descricao_bruta, r"(\d+)\s*su[íi]te")
        if not dados.get("banheiros"):
            dados["banheiros"] = self._extrair_numero_descricao(descricao_bruta, r"(\d+)\s*banheiro")
        if not dados.get("vagas"):
            dados["vagas"] = self._extrair_numero_descricao(descricao_bruta, r"(\d+)\s*vaga")
        if not dados.get("preco"):
            dados["preco"] = self._extrair_preco_descricao(descricao_bruta)
        if not dados.get("area_m2"):
            dados["area_m2"] = self._extrair_area_descricao(descricao_bruta)
        titulo = dados.get("titulo", "")
        texto_completo = titulo + "\n" + descricao_bruta
        if not dados.get("bairro_extraido"):
            dados["bairro_extraido"] = self._extrair_bairro_descricao(texto_completo)
        if not dados.get("cidade_extraida"):
            dados["cidade_extraida"] = self._extrair_cidade_descricao(texto_completo)

        # Normalizar campos numericos
        dados["preco"]        = self._normalizar_preco(dados.get("preco", ""))
        dados["area_m2"]      = self._normalizar_area(dados.get("area_m2", ""))
        dados["area_terreno"] = self._normalizar_area(dados.get("area_terreno", ""))
        dados["quartos"]      = self._normalizar_numero(dados.get("quartos", ""))
        dados["suites"]       = self._normalizar_numero(dados.get("suites", ""))
        dados["banheiros"]    = self._normalizar_numero(dados.get("banheiros", ""))
        dados["vagas"]        = self._normalizar_numero(dados.get("vagas", ""))
        dados["condominio"]   = self._normalizar_valor_taxa(dados.get("condominio", ""))
        dados["iptu"]         = self._normalizar_valor_taxa(dados.get("iptu", ""))
        dados["taxas"]        = self._normalizar_valor_taxa(dados.get("taxas", ""))
        dados["ano_construcao"] = self._normalizar_ano(dados.get("ano_construcao", ""))

        # Normalizar bairro e cidade (remover emojis, sufixos como "– João Pessoa/PB")
        dados["bairro_extraido"] = self._normalizar_localidade(dados.get("bairro_extraido", ""))
        dados["cidade_extraida"] = self._normalizar_localidade(dados.get("cidade_extraida", ""))

        # Características — normaliza lista textual e aplica fallback no modo browser.
        if isinstance(dados.get("caracteristicas"), str):
            dados["caracteristicas"] = self._normalizar_caracteristicas_texto(
                dados.get("caracteristicas", "")
            )
        if not dados.get("caracteristicas"):
            dados["caracteristicas"] = self._detectar_caracteristicas(
                titulo + "\n" + descricao_bruta
            )

        # Limpar valores inutils
        for k, v in dados.items():
            if isinstance(v, str) and v.lower().strip() in VALORES_VAZIOS:
                dados[k] = ""

        logger.debug(f"Dados extraídos: {dados}")
        dados = aplicar_tipos_imovel(dados)
        return dados

    def _extrair_bloco_descricao(self, texto):
        inicio = re.search(r"Descri[çc][aã]o\s*:", texto, re.IGNORECASE)
        if not inicio:
            return ""
        pos_inicio = inicio.end()
        proximo_rotulo = re.search(
            r"\n(?:Url\s+da\s+publica[çc][aã]o|Telefone\s+ou\s+WhatsApp|Usu[aá]rio\s+de?\s+Instagram\s+da|Tipo\s+de\s+im[oó]vel|Tipo\s+de\s+opera[çc][aã]o|Est[aá]gio\s+do\s+im[oó]vel|[EÉ]\s+t[eé]rreo\s+ou|Tem\s+Elevador\?|Quantos\s+Quartos\?|Quantas\s+Su[íi]tes?|[AÁ]rea\/Metros)",
            texto[pos_inicio:],
            re.IGNORECASE,
        )
        pos_fim = pos_inicio + proximo_rotulo.start() if proximo_rotulo else len(texto)
        return texto[pos_inicio:pos_fim].strip()

    def _extrair_valor(self, linhas, padrao):
        todos_padroes = list(ROTULOS.values())
        for i, linha in enumerate(linhas):
            if re.search(padrao, linha, re.IGNORECASE):
                valor = re.sub(padrao, "", linha, flags=re.IGNORECASE).strip()
                if valor:
                    return valor
                # Busca nas proximas linhas, mas ignora linhas que sao labels de outros campos
                for j in range(i + 1, min(i + 3, len(linhas))):
                    prox = linhas[j].strip()
                    if not prox:
                        continue
                    eh_label = any(re.search(p, prox, re.IGNORECASE) for p in todos_padroes)
                    if not eh_label:
                        return prox
                    break
        return ""

    def _gerar_titulo(self, descricao, dados: dict | None = None):
        """Gera título a partir dos dados estruturados ou da primeira linha da descrição."""
        # Título estruturado: monta com tipo + quartos + bairro/cidade
        if dados:
            tipo = str(dados.get("tipo_imovel") or "").strip()
            quartos = str(dados.get("quartos") or "").strip()
            bairro = str(dados.get("bairro_extraido") or "").strip()
            cidade = str(dados.get("cidade_extraida") or "").strip()
            area = str(dados.get("area_m2") or "").strip()
            partes = []
            if tipo:
                partes.append(tipo)
            if quartos and quartos not in ("0", ""):
                partes.append(f"{quartos} quarto{'s' if quartos != '1' else ''}")
            if area and area not in ("0", ""):
                partes.append(f"{area}m²")
            if bairro:
                partes.append(bairro)
            if cidade and cidade.lower() not in (bairro or "").lower():
                partes.append(cidade)
            if len(partes) >= 2:
                return " - ".join(partes)

        # Fallback: extrai da primeira linha da descrição
        if not descricao:
            return ""
        linhas = [l.strip() for l in descricao.split("\n") if l.strip()]
        if not linhas:
            return ""
        primeira_linha = linhas[0].strip('"\'')
        # Descarta linhas que parecem frases de call-to-action ou links
        import re as _re
        if _re.search(r'(entre em contato|chame|clique|acesse|link|http|@)', primeira_linha, _re.IGNORECASE):
            for linha in linhas[1:5]:
                linha = linha.strip('"\'')
                if len(linha) > 15 and not _re.search(r'(entre em contato|chame|clique|acesse|link|http|@)', linha, _re.IGNORECASE):
                    primeira_linha = linha
                    break
        if "." in primeira_linha:
            return primeira_linha.split(".")[0].strip() + "."
        return primeira_linha

    def _limpar_texto_estranho(self, texto):
        # Remover caracteres CJK (chines, japones, coreano)
        texto = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', texto)
        # Remover URLs (http/https/wa.me)
        texto = re.sub(r'https?://\S+', '', texto)
        texto = re.sub(r'wa\.me/\S+', '', texto)
        texto = re.sub(r'@\w+', '', texto)
        texto = re.sub(r'.*(?:instagram|whatsapp|telefone|celular|contato)\s*:?.*', '', texto, flags=re.IGNORECASE)
        texto = re.sub(
            r'^\D*(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}\D*$',
            '',
            texto,
            flags=re.MULTILINE,
        )
        # Remover linhas com "não informado" ou variações
        texto = re.sub(r'.*(n[ãa]o\s+informad[ao]|sem\s+informa[çc][ãa]o|n[ãa]o\s+foi\s+informad[ao]).*', '', texto, flags=re.IGNORECASE)
        # Remover linhas que ficaram so com o rotulo sem valor util (ex: "Link da publicação", "Usuário:", "WhatsApp:")
        texto = re.sub(r'^\s*(link\s+da\s+publica[çc][ãa]o|usu[áa]rio\s*:?|whatsapp\s*:?|contato\s+e\s+link\s*:?|telefone\s*:?)\s*$', '', texto, flags=re.IGNORECASE | re.MULTILINE)
        # Remover linhas que ficaram so com numeros soltos ou vazias apos limpeza
        linhas = [l for l in texto.split('\n') if not re.match(r'^\s*\d+\s*$', l)]
        return '\n'.join(linhas).strip()

    def _extrair_numero_descricao(self, texto, padrao):
        match = re.search(padrao, texto, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extrair_preco_descricao(self, texto):
        # 1) R$ 170.000,00 ou R$170.000
        match = re.search(r'R\$\s*([\d.,]+)', texto, re.IGNORECASE)
        if match:
            return match.group(1)
        # 2) "valor: 170.000" ou "preço: 170000" ou "venda: 350.000"
        match = re.search(
            r'(?:valor|pre[çc]o|venda|aluguel|mensalidade)\s*:?\s*R?\$?\s*([\d.,]{4,})',
            texto, re.IGNORECASE
        )
        if match:
            return match.group(1)
        # 3) Número formatado com separador de milhar (ex: 350.000 ou 1.500.000).
        # Não usa \d{5,} para evitar capturar CRM, CRECI, telefone ou outros códigos soltos.
        for m in re.finditer(r'\b(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?)\b', texto):
            val = re.sub(r'[^\d]', '', m.group(1))
            if len(val) >= 5:  # pelo menos R$10.000
                return m.group(1)
        return ""

    def _extrair_area_descricao(self, texto):
        match = re.search(r'(\d+(?:[.,]\d+)?)\s*m[²2]', texto, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extrair_bairro_descricao(self, texto):
        # Buscar padroes como "em Mangabeira" ou "no Bairro X"
        match = re.search(r'(?:em|no bairro|bairro)\s+([A-ZÀ-Ú][a-zA-ZÀ-ú\s]+?)(?:\s*[–\-,]|\s*\n|$)', texto)
        if match:
            bairro = match.group(1).strip()
            if len(bairro) > 3 and len(bairro) < 40:
                return bairro
        return ""

    def _extrair_cidade_descricao(self, texto):
        match = re.search(r'([A-ZÀ-Ú][a-zA-ZÀ-ú\s]+?)\s*/\s*[A-Z]{2}', texto)
        if match:
            return match.group(1).strip()
        return ""

    def _normalizar_localidade(self, valor):
        if not valor:
            return ""
        # Remover emojis e caracteres nao-textuais
        valor = re.sub(r'[^\w\sÀ-ÿ\-]', '', valor)
        # Pegar apenas a parte antes de separadores como –, /, ,
        valor = re.split(r'[–/,]', valor)[0]
        # Remover sufixos de UF como " PB", " CE", etc.
        valor = re.sub(r'\s+[A-Z]{2}\s*$', '', valor.strip())
        valor = valor.strip()
        return valor if len(valor) > 2 else ""

    def _normalizar_preco(self, valor):
        if not valor:
            return ""
        valor = str(valor).strip()
        # Remove prefixos textuais: "a partir de R$ 590 mil" → "590 mil"
        valor = re.sub(r'^(?:a partir de|a partir|partir de|valor|preço|preco)\s*', '', valor, flags=re.IGNORECASE).strip()
        valor = re.sub(r'^R\$\s*', '', valor, flags=re.IGNORECASE).strip()

        # "350 mil", "3,5 mil", "1.5k" — busca em qualquer posição na string
        mil = re.search(r'([\d.,]+)\s*(?:mil|k)\b', valor, re.IGNORECASE)
        if mil:
            num = mil.group(1).replace(',', '.')
            partes = num.split('.')
            if len(partes) > 1 and len(partes[-1]) == 3:
                num = ''.join(partes)  # "1.500 mil" → usa "1500"
            try:
                return str(int(float(num) * 1000))
            except Exception:
                pass

        # Remove decimal: tudo após vírgula (formato BR) independente de quantos dígitos
        valor = re.sub(r',\d+$', '', valor)
        # Remove decimal US .XX com exatamente 2 dígitos no final
        valor = re.sub(r'\.\d{2}$', '', valor)

        apenas_digitos = re.sub(r'[^\d]', '', valor)

        # Sanidade: preço imobiliário BR entre 4 e 8 dígitos (R$1.000 a R$99.999.999)
        # Mais de 8 dígitos → provavelmente telefone, CRECI ou código — descarta
        if apenas_digitos and apenas_digitos != '0':
            if len(apenas_digitos) > 8:
                return ""
            if len(apenas_digitos) >= 4:
                return apenas_digitos

        return ""

    def _normalizar_valor_taxa(self, valor):
        if not valor:
            return ""
        valor = str(valor).strip()
        valor = re.sub(r'^(?:condom[íi]nio|iptu|taxas?|valor)\s*', '', valor, flags=re.IGNORECASE).strip()
        valor = re.sub(r'^R\$\s*', '', valor, flags=re.IGNORECASE).strip()
        valor = re.sub(r',\d+$', '', valor)
        valor = re.sub(r'\.\d{2}$', '', valor)
        apenas_digitos = re.sub(r'[^\d]', '', valor)
        if apenas_digitos and apenas_digitos != '0' and len(apenas_digitos) <= 8:
            return apenas_digitos
        return ""

    def _normalizar_area(self, valor):
        if not valor:
            return ""
        numeros = re.findall(r"\d+(?:[.,]\d+)?", valor)
        if not numeros:
            return ""
        floats = [float(n.replace(",", ".")) for n in numeros]
        inteiro = int(max(floats))
        return str(inteiro) if inteiro > 0 else ""

    def _normalizar_numero(self, valor):
        if not valor:
            return ""
        match = re.search(r"\d+", valor)
        if not match:
            return ""
        num = int(match.group())
        return str(num) if num > 0 else ""

    def _normalizar_ano(self, valor):
        if not valor:
            return ""
        match = re.search(r"\b(19|20)\d{2}\b", str(valor))
        return match.group() if match else ""

    def _detectar_caracteristicas(self, texto: str) -> list[str]:
        """Detecta características pelo texto livre (usado no modo browser)."""
        if not texto:
            return []
        texto_lower = texto.lower()
        encontradas = []
        for keywords, nome in _FEATURES_KEYWORDS:
            if any(kw in texto_lower for kw in keywords):
                encontradas.append(nome)
        return sorted(dict.fromkeys(encontradas), key=lambda s: s.lower())

    def _normalizar_caracteristicas_texto(self, texto: str) -> list[str]:
        """Converte a linha 'Caracteristicas:' do modo browser em lista limpa."""
        if not texto:
            return []
        permitidas = {self._norm_texto(nome): nome for _, nome in _FEATURES_KEYWORDS}
        itens = re.split(r"[,;\n]+", texto)
        encontradas = []
        for item in itens:
            item_norm = re.sub(r"^[\-\*\d\.\)\s]+", "", item).strip()
            if not item_norm:
                continue
            alvo = self._norm_texto(item_norm)
            if alvo in permitidas:
                encontradas.append(permitidas[alvo])
                continue
            for chave, oficial in permitidas.items():
                if alvo == chave or alvo in chave or chave in alvo:
                    encontradas.append(oficial)
                    break
        return sorted(dict.fromkeys(encontradas), key=lambda s: s.lower())

    @staticmethod
    def _norm_texto(texto: str) -> str:
        texto = unicodedata.normalize("NFKD", str(texto or ""))
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", texto.lower()).strip()


