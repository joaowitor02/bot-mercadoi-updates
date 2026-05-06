"""
Modulo cliente do DeepSeek via navegador (chat.deepseek.com).
"""

import os
import sys
from playwright.async_api import async_playwright
from modules.logger import Logger

logger = Logger("deepseek_client")

DEEPSEEK_URL = "https://chat.deepseek.com"

PROMPT_FIXO = """Analise a publicacao do Instagram no link abaixo e responda EXATAMENTE neste formato, sem explicacoes extras.
Importante: nao invente informacoes. Tudo que nao estiver especificado deve ficar vazio. Nao mostre Instagram, celular, WhatsApp, nome de pessoa ou chamada de contato dentro da Descricao.

Titulo: [primeira linha/frase da publicacao que resume o principal do imovel]
Descricao:
[descricao da publicacao, o mais fiel possivel, preservando quebras de linha, sem nome de pessoa, Instagram, celular, WhatsApp ou contato]
Url da publicacao: [url completa]
Telefone ou WhatsApp: [https://wa.me/+55... se existir; senao vazio]
Usuario de Instagram da publicacao: [url completa do perfil se existir; senao vazio]
Tipo de imovel: [Apartamento, Casa, Casa de Condominio, Terreno, Sala Comercial, Apto. Cobertura, Apto. Duplex etc.]
Tipo de operacao: [A Venda ou Em Aluguel]
Preco: [apenas numeros; nunca telefone/CRECI/codigo]
Caracteristicas: [liste em ordem alfabetica somente itens da lista permitida claramente mencionados]
Estagio do imovel: [breve lancamento, lancamento, em construcao, novo, seminovo, usado ou reformado]
E terreo ou qual andar?: [Terreo, numero do andar ou vazio]
Tem Elevador?: [Sim, Nao ou vazio]
Quantos Quartos?: [numero]
Quantas Suites: [numero]
Banheiros: [numero]
Vagas (garagem): [numero]
Area/Metros quadrados (m2): [numero]
Area do terreno: [numero]
Condominio: [apenas numeros]
IPTU: [apenas numeros]
Taxas: [apenas numeros]
Posicao Solar: [texto curto]
Perto do mar?: [Vista para o mar, Frente para o mar, Quadra do mar, Proximo ao mar ou vazio]
Posicao no Predio: [Frente, Fundo, Lateral, Meio ou vazio]
Mobiliado?: [sem mobilia, semi mobiliado, Mobiliado, Mobiliado e Decorado ou vazio]
Escriturado?: [Sim, Nao ou vazio]
Aceita Permuta?: [Sim, Nao ou vazio]
Aceita Airbnb/Temporada?: [Sim, Nao ou vazio]
Aceita Financiamento?: [Sim, Nao ou vazio]
Proximidades: [lugares/vizinhancas de referencia]
Endereco: [rua e numero se mencionados; senao vazio]
CEP: [CEP se mencionado explicitamente; senao vazio]
Cidade: [nome da cidade]
Bairro: [nome do bairro]

Caracteristicas permitidas:
Academia; Acesso para cadeirantes; Area de Lazer; Area pet; Area Verde; Banheiro social; Biblioteca; Bicicletario; Campo de futebol; Campo de Golf; Churrasqueira; Circuito de seguranca; Condominio fechado; Coworking; Espaco gourmet; Espaco kids; Estacionamento para visita; Gerador Eletrico; Lavanderia; Lounge; Mini mercado; Piscina adulto; Piscina infantil; Playground; Portaria; Portaria 24h; Portaria eletronica; Quadra de areia; Quadra de tenis; Quadra poliesportiva; Recepcao; Salao de festas / SUM; Salao de jogos; Sauna; Seguranca 24h; Sistema de alarme; Solarium; Spa; Terraco/Rooftop; Vaga coberta; Vestiario; Aceita animais; Agua inclusa; Aquecedor; Aquecimento central; Ar Condicionado; Area de servico; Area externa privativa; Churrasqueira propria; Closet; Conexao a internet; Cozinha; Cozinha americana; Cozinha Gourmet; Cozinha independente; DCE - Dependencia de empregada; Deposito; Despensa; Energia solar; Entrada de servico; Escritorio; Espaco Gourmet; Freezer; Gas Central; Geladeira; Gramado / Jardim; Hidromassagem/Jacuzzi; Interfone; Jacuzzi; Lareira; Lava-louca; Lavadora de roupas; Mezanino; Microondas; Mobiliado; Piscina Privativa; Porteira Fechada; Projetados; Sala de estar; Sala em 2 ambientes; Suite; Telefone; TV; TV a cabo; Varanda; Varanda gourmet; Varanda Integrada; Ventilado.

Link: {url}"""


def _headless() -> bool:
    return sys.platform != "win32"


def _chrome_args() -> list[str]:
    if _headless():
        return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    return ["--start-maximized"]


def _chrome_path() -> str | None:
    if sys.platform != "win32":
        return None
    caminhos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    return next((p for p in caminhos if os.path.exists(p)), None)


class DeepSeekClient:
    def __init__(self, deepseek_profile_path=""):
        self.profile_path = deepseek_profile_path if sys.platform == "win32" else ""

    async def extrair(self, url):
        prompt = PROMPT_FIXO.format(url=url)
        try:
            async with async_playwright() as p:
                launch_kwargs = dict(
                    headless=_headless(),
                    args=_chrome_args(),
                )
                chrome = _chrome_path()
                if chrome:
                    launch_kwargs["executable_path"] = chrome

                if self.profile_path:
                    browser = await p.chromium.launch_persistent_context(
                        user_data_dir=self.profile_path,
                        **launch_kwargs,
                    )
                else:
                    _browser = await p.chromium.launch(**launch_kwargs)
                    browser = await _browser.new_context()
                    browser._browser_obj = _browser
                page = browser.pages[0] if browser.pages else await browser.new_page()
                logger.info(f"Abrindo DeepSeek... (headless={_headless()})")
                await page.goto(DEEPSEEK_URL, timeout=30000)
                try:
                    await page.wait_for_selector(
                        'textarea[placeholder*="Message"], textarea[placeholder*="mensagem"], #chat-input, textarea',
                        timeout=15000
                    )
                except Exception:
                    pass
                if not await self._esta_logado(page):
                    logger.error("DeepSeek nao esta logado.")
                    await browser.close()
                    return None
                await self._nova_conversa(page)
                logger.info("Enviando prompt ao DeepSeek...")
                await self._enviar_prompt(page, prompt)
                logger.info("Aguardando resposta do DeepSeek...")
                resposta = await self._aguardar_resposta(page)
                await browser.close()
                if resposta:
                    logger.info(f"Resposta recebida ({len(resposta)} caracteres)")
                    logger.info(f"Previa: {resposta[:300]}")
                else:
                    logger.error("Resposta vazia do DeepSeek")
                return resposta
        except Exception as e:
            logger.error(f"Erro no DeepSeekClient: {e}")
            return None

    async def _esta_logado(self, page):
        try:
            login_btn = await page.query_selector('button:has-text("Log in"), a:has-text("Sign in")')
            return login_btn is None
        except Exception:
            return True

    async def _nova_conversa(self, page):
        try:
            seletores = [
                'button:has-text("New chat")',
                'button:has-text("Nova conversa")',
                'a:has-text("New chat")',
            ]
            for sel in seletores:
                elem = await page.query_selector(sel)
                if elem:
                    await elem.click()
                    try:
                        await page.wait_for_selector(
                            'textarea[placeholder*="Message"], textarea[placeholder*="mensagem"], #chat-input, textarea',
                            timeout=5000
                        )
                    except Exception:
                        await page.wait_for_timeout(500)
                    return
        except Exception as e:
            logger.warning(f"Nao foi possivel criar nova conversa: {e}")

    async def _enviar_prompt(self, page, prompt):
        seletores_input = [
            'textarea[placeholder*="Message"]',
            'textarea[placeholder*="mensagem"]',
            '#chat-input',
            'div[contenteditable="true"]',
            'textarea',
        ]
        campo = None
        for sel in seletores_input:
            campo = await page.query_selector(sel)
            if campo:
                break
        if not campo:
            raise Exception("Campo de texto do DeepSeek nao encontrado")
        await campo.click()
        await page.wait_for_timeout(100)
        tag = await campo.evaluate("el => el.tagName.toLowerCase()")
        if tag == "textarea":
            await campo.fill(prompt)
        else:
            await page.keyboard.press("Control+A")
            await page.keyboard.type(prompt)
        await page.wait_for_timeout(200)
        seletores_enviar = [
            'button[type="submit"]',
            'button:has-text("Send")',
            'button[aria-label*="Send"]',
        ]
        for sel in seletores_enviar:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                return
        await campo.press("Enter")

    async def _aguardar_resposta(self, page, timeout=120):
        await page.wait_for_timeout(800)
        seletores_loading = [
            'button[aria-label*="Stop"]',
            'button:has-text("Stop")',
            'button:has-text("Parar")',
        ]
        logger.info("Aguardando resposta completa...")
        elapsed = 0
        while elapsed < timeout:
            gerando = False
            for sel in seletores_loading:
                elem = await page.query_selector(sel)
                if elem:
                    gerando = True
                    break
            if not gerando and elapsed > 2:
                await page.wait_for_timeout(800)
                break
            await page.wait_for_timeout(1000)
            elapsed += 1
        return await self._coletar_resposta(page)

    async def _coletar_resposta(self, page):
        seletores_resposta = [
            ".ds-markdown:last-of-type",
            'div[class*="markdown"]:last-of-type',
            ".message:last-child .content",
        ]
        texto_anterior = ""
        estavel = 0
        for _ in range(15):
            await page.wait_for_timeout(800)
            for sel in seletores_resposta:
                try:
                    elems = await page.query_selector_all(sel)
                    if elems:
                        texto = await elems[-1].inner_text()
                        if texto and len(texto) > 50:
                            if texto.strip() == texto_anterior:
                                estavel += 1
                                if estavel >= 2:
                                    return texto.strip()
                            else:
                                estavel = 0
                            texto_anterior = texto.strip()
                            break
                except Exception:
                    pass
        try:
            todas = await page.query_selector_all('[class*="message"], [class*="content"]')
            if todas:
                ultimo = todas[-1]
                texto = await ultimo.inner_text()
                if texto and len(texto) > 50:
                    return texto.strip()
        except Exception:
            pass
        logger.error("Nao foi possivel extrair a resposta do DeepSeek")
        return None
