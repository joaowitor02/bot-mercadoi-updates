"""
Modulo cliente do DeepSeek via navegador (chat.deepseek.com).
"""

from playwright.async_api import async_playwright
from modules.logger import Logger

logger = Logger("deepseek_client")

DEEPSEEK_URL = "https://chat.deepseek.com"

PROMPT_FIXO = """Analise a publicação do Instagram no link abaixo e responda EXATAMENTE neste formato, sem explicações extras:

Título: [crie um título atraente e direto para o anúncio imobiliário, ex: "Apartamento 3 Quartos à Venda no Bessa – João Pessoa/PB"]
Descrição:
[crie uma descrição completa, bonita e organizada do imóvel, usando emojis relevantes (🏠🛏️🚿🚗📐💰📍etc), separando as informações em tópicos com quebras de linha. Inclua todos os detalhes disponíveis: características, diferenciais, localização, contato e link. Seja o mais completo possível.]
Url da publicação: [url completa]
Telefone ou WhatsApp: [no formato https://wa.me/+55...]
Usuário de Instagram da publicação: [url completa do perfil]
Tipo de imóvel: [ex: Apartamento]
Tipo de operação: [A Venda ou Em Aluguel]
Preço: [apenas números]
Estágio do imóvel: [novo/usado/em construção]
É térreo ou qual andar?: [ex: Térreo ou 3º andar]
Tem Elevador?: [Sim ou Não]
Quantos Quartos?: [número]
Quantas Suites: [número]
Banheiros: [número]
Vagas (garagem): [número]
Area/Metros quadrados (m2): [número]
Cidade: [nome da cidade]
Bairro: [nome do bairro]

Link: {url}"""

CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"


class DeepSeekClient:
    def __init__(self, deepseek_profile_path="C:\\chrome_bot_deepseek"):
        self.profile_path = deepseek_profile_path

    async def extrair(self, url):
        prompt = PROMPT_FIXO.format(url=url)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_path,
                    headless=False,
                    executable_path=CHROME_PATH,
                    args=["--start-maximized"],
                )
                page = browser.pages[0] if browser.pages else await browser.new_page()
                logger.info("Abrindo DeepSeek...")
                await page.goto(DEEPSEEK_URL, timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=20000)
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
                    logger.info(f"Prévia: {resposta[:300]}")
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
                    await page.wait_for_timeout(1500)
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
        await page.wait_for_timeout(500)
        tag = await campo.evaluate("el => el.tagName.toLowerCase()")
        if tag == "textarea":
            await campo.fill(prompt)
        else:
            await page.keyboard.press("Control+A")
            await page.keyboard.type(prompt)
        await page.wait_for_timeout(500)
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
        await page.wait_for_timeout(2000)
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
            if not gerando and elapsed > 3:
                await page.wait_for_timeout(2000)
                break
            await page.wait_for_timeout(2000)
            elapsed += 2
        return await self._coletar_resposta(page)

    async def _coletar_resposta(self, page):
        seletores_resposta = [
            '.ds-markdown:last-of-type',
            'div[class*="markdown"]:last-of-type',
            '.message:last-child .content',
        ]
        # Aguarda a resposta estabilizar (para de crescer)
        texto_anterior = ""
        for _ in range(10):
            await page.wait_for_timeout(2000)
            for sel in seletores_resposta:
                try:
                    elems = await page.query_selector_all(sel)
                    if elems:
                        texto = await elems[-1].inner_text()
                        if texto and len(texto) > 50:
                            if texto.strip() == texto_anterior:
                                return texto.strip()
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