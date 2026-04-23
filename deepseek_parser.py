"""
Modulo parser da resposta do DeepSeek.
Extrai campos estruturados do texto e tambem faz extracao inteligente
quando os dados estao dentro da descricao.
"""

import re
from modules.logger import Logger

logger = Logger("deepseek_parser")

ROTULOS = {
    "titulo": r"T[íi]tulo\s*:",
    "url_publicacao": r"Url\s+da\s+publica[çc][aã]o\s*:",
    "whatsapp_url": r"Telefone\s+ou\s+WhatsApp\s*:",
    "instagram_url": r"Usu[aá]rio\s+de?\s+Instagram\s+da\s+publica[çc][aã]o\s*:",
    "tipo_imovel": r"Tipo\s+de\s+im[oó]vel\s*:",
    "operacao": r"Tipo\s+de\s+opera[çc][aã]o\s*:|Opera[çc][aã]o\s*:",
    "preco": r"Pre[çc]o\s*:",
    "estagio_imovel": r"Est[aá]gio\s+do\s+im[oó]vel\s*:",
    "andar": r"[EÉ]\s+t[eé]rreo\s+ou\s+qual\s+andar\??\s*:|Andar\s*:",
    "elevador": r"Tem\s+Elevador\??\s*:",
    "quartos": r"Quantos\s+Quartos\??\s*:|Quartos\s*:",
    "suites": r"Quantas\s+Su[íi]tes?\s*:|Su[íi]tes?\s*:",
    "banheiros": r"Banheiros\s*:",
    "vagas": r"Vagas?\s*(?:\(garagem\))?\s*:",
    "area_m2": r"[AÁ]rea\/Metros\s+quadrados\s*(?:\(m2\))?\s*:|Tamanho\s*:",
    "cidade_extraida": r"Cidade\s*:",
    "bairro_extraido": r"Bairro\s*:",
}

VALORES_VAZIOS = {"0", "não informado", "nao informado", "campo vazio", "-", "n/a", "não há", "nao ha", ""}


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

        # Fallback de titulo: usar primeira linha da descricao
        if not dados.get("titulo"):
            dados["titulo"] = self._gerar_titulo(descricao_bruta)

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
        dados["preco"] = self._normalizar_preco(dados.get("preco", ""))
        dados["area_m2"] = self._normalizar_area(dados.get("area_m2", ""))
        dados["quartos"] = self._normalizar_numero(dados.get("quartos", ""))
        dados["suites"] = self._normalizar_numero(dados.get("suites", ""))
        dados["banheiros"] = self._normalizar_numero(dados.get("banheiros", ""))
        dados["vagas"] = self._normalizar_numero(dados.get("vagas", ""))

        # Normalizar bairro e cidade (remover emojis, sufixos como "– João Pessoa/PB")
        dados["bairro_extraido"] = self._normalizar_localidade(dados.get("bairro_extraido", ""))
        dados["cidade_extraida"] = self._normalizar_localidade(dados.get("cidade_extraida", ""))

        # Limpar valores inutils
        for k, v in dados.items():
            if isinstance(v, str) and v.lower().strip() in VALORES_VAZIOS:
                dados[k] = ""

        logger.debug(f"Dados extraídos: {dados}")
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

    def _gerar_titulo(self, descricao):
        if not descricao:
            return ""
        linhas = [l.strip() for l in descricao.split("\n") if l.strip()]
        if not linhas:
            return ""
        primeira_linha = linhas[0]
        # Remover aspas do inicio
        primeira_linha = primeira_linha.strip('"\'')
        if "." in primeira_linha:
            return primeira_linha.split(".")[0].strip() + "."
        return primeira_linha

    def _limpar_texto_estranho(self, texto):
        # Remover caracteres CJK (chines, japones, coreano)
        texto = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', texto)
        # Remover URLs (http/https/wa.me)
        texto = re.sub(r'https?://\S+', '', texto)
        texto = re.sub(r'wa\.me/\S+', '', texto)
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
        # 1) R$ 170.000,00 ou R$170000
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
        # 3) número grande solto que parece preço (>= 50.000 ou >= 50000)
        for m in re.finditer(r'\b(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{5,})\b', texto):
            val = re.sub(r'[^\d]', '', m.group(1))
            if len(val) >= 5:   # >= 10.000
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
        # Remove prefixo R$ e espaços
        valor = re.sub(r'^R\$\s*', '', valor.strip(), flags=re.IGNORECASE).strip()

        # "350 mil", "3,5 mil", "1.5k" → multiplica por 1000
        mil = re.match(r'^([\d.,]+)\s*(?:mil|k)\b', valor, re.IGNORECASE)
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
        if apenas_digitos and apenas_digitos != '0':
            if len(apenas_digitos) > 8:
                return apenas_digitos[:8]
            if len(apenas_digitos) >= 4:
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


