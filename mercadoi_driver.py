"""
Modulo de automacao do formulario do Mercadoi.
"""

import json
import os
import unicodedata
import re
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from modules.logger import Logger

logger = Logger("mercadoi_driver")


def normalizar(texto):
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.lower().strip())


# Mapeamento bairro → cidade para municípios da Paraíba
_BAIRRO_CIDADE_PB: dict[str, str] = {
    # João Pessoa
    "manaira": "João Pessoa", "tambau": "João Pessoa", "cabo branco": "João Pessoa",
    "miramar": "João Pessoa", "bessa": "João Pessoa", "torre": "João Pessoa",
    "bancarios": "João Pessoa", "mangabeira": "João Pessoa", "valentina": "João Pessoa",
    "brisamar": "João Pessoa", "jardim oceania": "João Pessoa", "oceania": "João Pessoa",
    "estados": "João Pessoa", "bairro dos estados": "João Pessoa",
    "epitacio pessoa": "João Pessoa", "altiplano": "João Pessoa",
    "roger": "João Pessoa", "jaguaribe": "João Pessoa", "geisel": "João Pessoa",
    "cristo redentor": "João Pessoa", "castelo branco": "João Pessoa",
    "agua fria": "João Pessoa", "cruz das armas": "João Pessoa",
    "funcionarios": "João Pessoa", "expedicionarios": "João Pessoa",
    "tambia": "João Pessoa", "varadouro": "João Pessoa", "trincheiras": "João Pessoa",
    "pedro gondim": "João Pessoa", "aeroclube": "João Pessoa", "grotao": "João Pessoa",
    "cidade universitaria": "João Pessoa", "anatolia": "João Pessoa",
    "jose americo": "João Pessoa", "planalto": "João Pessoa",
    "cuia": "João Pessoa", "paratibe": "João Pessoa", "gramame": "João Pessoa",
    "mussumagro": "João Pessoa", "portal do sol": "João Pessoa",
    "costa e silva": "João Pessoa", "jose bezerra": "João Pessoa",
    "mandacaru": "João Pessoa", "san martin": "João Pessoa",
    "jardim luna": "João Pessoa", "alto do ceu": "João Pessoa",
    "penha": "João Pessoa", "ilha do bispo": "João Pessoa", "rangel": "João Pessoa",
    "13 de maio": "João Pessoa", "treze de maio": "João Pessoa",
    "jardim sao paulo": "João Pessoa", "padre zé": "João Pessoa", "padre ze": "João Pessoa",
    "conjunto ceará": "João Pessoa", "conjunto ceara": "João Pessoa",
    "Paulo VI": "João Pessoa", "paulo vi": "João Pessoa",
    "novo horizonte joao pessoa": "João Pessoa",
    # Cabedelo
    "poco": "Cabedelo", "poca": "Cabedelo", "bairro do poco": "Cabedelo",
    "ponta de mato": "Cabedelo", "renascer": "Cabedelo",
    "ponta de cabedelo": "Cabedelo", "intermares": "Cabedelo",
    "jardins cabedelo": "Cabedelo", "camalau": "Cabedelo",
    "centro cabedelo": "Cabedelo",
    # Campina Grande
    "bodocongo": "Campina Grande", "jose pinheiro": "Campina Grande",
    "dinamarca": "Campina Grande", "liberdade": "Campina Grande",
    "sandra cavalcante": "Campina Grande", "malvinas": "Campina Grande",
    "catole": "Campina Grande", "prata": "Campina Grande",
    "bela vista campina": "Campina Grande", "palmeira campina": "Campina Grande",
    "universitario campina": "Campina Grande", "miriam coelho": "Campina Grande",
    "serrotao": "Campina Grande", "monte castelo": "Campina Grande",
    "centenario": "Campina Grande", "itararé": "Campina Grande", "itarare": "Campina Grande",
    # Bayeux
    "bayeux": "Bayeux", "miramar bayeux": "Bayeux",
    # Santa Rita
    "santa rita": "Santa Rita", "várzea nova": "Santa Rita", "varzea nova": "Santa Rita",
    # Conde
    "jacuma": "Conde", "tabatinga": "Conde", "coqueirinho": "Conde",
    "barra de camaratuba": "Conde", "jacumã": "Conde",
    # Lucena
    "lucena": "Lucena", "fagundes": "Lucena",
    # Pitimbu
    "pitimbu": "Pitimbu", "acaua": "Pitimbu", "praia de pitimbu": "Pitimbu",
    # Alhandra
    "alhandra": "Alhandra",
    # Mamanguape
    "mamanguape": "Mamanguape",
    # Rio Tinto
    "rio tinto": "Rio Tinto",
    # Sapé
    "sape": "Sapé",
    # Guarabira
    "guarabira": "Guarabira",
    # Patos
    "patos": "Patos",
    # Sousa
    "sousa": "Sousa",
    # Cajazeiras
    "cajazeiras": "Cajazeiras",
}


class MercadoiDriver:
    def __init__(self, base_url, profile_path=r"C:\chrome_bot_mercadoi", execution_id: str = ""):
        self.base_url = base_url
        self.profile_path = profile_path
        self.execution_id = execution_id
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp("http://localhost:9222")
        contexts = self._browser.contexts
        if not contexts:
            raise Exception("Chrome do Mercadoi conectado, mas sem contexto disponivel")
        self._context = contexts[0]
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        logger.info("Conectado ao Chrome do Mercadoi ja aberto na porta 9222")
        return self

    async def __aexit__(self, *args):
        # Nao fechar o contexto: ele pertence ao Chrome aberto/logado pelo usuario.
        # Encerrar aqui derrubaria a sessao que o bot reutiliza entre execucoes.
        if self._playwright:
            await self._playwright.stop()

    async def _garantir_login(self, page):
        await page.goto(self.base_url, timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        logado = await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu')
        if logado:
            return
        logger.info("Nao logado. Abrindo pagina de login — aguardando ate 120s para login manual...")
        await page.goto(f"{self.base_url}/login/", timeout=30000)
        for _ in range(60):
            await page.wait_for_timeout(2000)
            logado = await page.query_selector('a[href*="logout"], a[href*="dashboard"], .user-menu, #user-menu')
            if logado:
                logger.info("Login detectado, continuando...")
                return
        raise Exception("Timeout aguardando login no Mercadoi")

    async def preencher_e_salvar(self, dados, tipo_midia, arquivo_midia):
        page = self._page
        await self._garantir_login(page)
        resultado = {
            "sucesso": False,
            "mensagem": "",
            "cidade_aplicada": "",
            "bairro_aplicado": "",
            "status_erro": "",
            "mercadoi_url": "",
        }

        try:
            await page.goto(f"{self.base_url}/create-a-listing/", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector('#prop_title', timeout=15000)
            except Exception:
                logger.warning("Campo #prop_title demorou para aparecer, continuando mesmo assim")

            # TITULO
            titulo = dados.get("titulo", "").strip()
            if not titulo:
                resultado["status_erro"] = "erro_preenchimento"
                resultado["mensagem"] = "Titulo ausente"
                return resultado
            await page.fill('#prop_title', titulo)
            logger.info(f"Titulo preenchido: {titulo}")

            # CONTEUDO
            conteudo = self._montar_conteudo(dados)
            await self._preencher_editor(page, conteudo)

            # TIPO DE IMOVEL
            tipo_imovel = self._normalizar_tipo_imovel(dados.get("tipo_imovel", ""))
            await self._selecionar_tipo_imovel(page, tipo_imovel)

            # OPERACAO
            operacao = dados.get("operacao", "").strip() or "A Venda"
            await self._selecionar_por_texto(page, '#prop_status', operacao)

            # Preenche todos os campos numéricos em uma única chamada JS
            await self._preencher_campos_batch(page, {
                '#prop_price':  dados.get("preco",    "").strip(),
                '#prop_beds':   dados.get("quartos",  "").strip(),
                '#prop_rooms':  dados.get("suites",   "").strip(),
                '#prop_baths':  dados.get("banheiros","").strip(),
                '#prop_garage': dados.get("vagas",    "").strip(),
                '#prop_size':   dados.get("area_m2",  "").strip(),
            })

            # Seleciona todos os selects simples em uma única chamada JS
            await self._selecionar_batch(page, [
                ('select[name="estagio-da-obra-imc3b3vel[]"]', dados.get("estagio_imovel","").strip()),
                ('select[name="no-tc3a9rreo[]"]',              dados.get("andar",         "").strip()),
                ('select[name="tem-elevador"]',                dados.get("elevador",      "").strip()),
            ])

            # Faz Parceria — sempre "A combinar", com retry por timing de select2
            for _tentativa in range(4):
                try:
                    await page.select_option('select[name*="parcer"]', label="A combinar", timeout=3000)
                    logger.info("Selecionado 'A combinar' em faz-parceria")
                    break
                except Exception:
                    if _tentativa < 3:
                        await page.wait_for_timeout(200)
                    else:
                        logger.info("Faz-parceria nao selecionado apos 4 tentativas")

            # CIDADE
            cidade = dados.get("cidade_extraida", "").strip()
            bairro = dados.get("bairro_extraido", "").strip()
            cidade_aplicada = await self._selecionar_cidade(page, cidade, bairro)
            resultado["cidade_aplicada"] = cidade_aplicada

            # BAIRRO
            bairro_aplicado = await self._selecionar_bairro(page, bairro)
            resultado["bairro_aplicado"] = bairro_aplicado

            # MIDIA
            if arquivo_midia:
                validos = [f for f in arquivo_midia if os.path.exists(f)]
                if validos:
                    upload_ok = await self._anexar_midia(page, validos)
                    if not upload_ok:
                        resultado["status_erro"] = "erro_upload"
                        resultado["mensagem"] = "Falha ao anexar midia no Mercadoi"
                        resultado["screenshot_path"] = await self._tirar_screenshot("erro_upload")
                        return resultado
                    if tipo_midia == "video":
                        logger.info(f"Video: {len(validos)} frame(s) extraido(s) anexados")
                else:
                    logger.warning("Nenhum arquivo de midia valido encontrado")
                    resultado["status_erro"] = "erro_upload"
                    resultado["mensagem"] = "Nenhum arquivo de midia valido encontrado"
                    return resultado

            # NAO EXIBIR CONTATO
            await self._marcar_nao_exibir_contato(page)

            # DECIDE: publicar direto ou salvar rascunho
            publicar_direto = (
                tipo_midia == "imagem"
                and bool(dados.get("preco", "").strip())
                and bool(dados.get("tipo_imovel", "").strip())
            )

            if publicar_direto:
                logger.info("Criterios atendidos — publicando diretamente")
                salvamento = await self._publicar(page)
                modo = "Publicado"
                if not salvamento.get("ok"):
                    logger.warning("Publicacao direta falhou — salvando como rascunho")
                    salvamento = await self._salvar_rascunho(page)
                    modo = "Rascunho salvo"
            else:
                motivos = []
                if tipo_midia != "imagem":
                    motivos.append(f"midia={tipo_midia}")
                if not dados.get("preco", "").strip():
                    motivos.append("sem preco")
                if not dados.get("tipo_imovel", "").strip():
                    motivos.append("sem tipo")
                logger.info(f"Salvando como rascunho ({', '.join(motivos)})")
                salvamento = await self._salvar_rascunho(page)
                modo = "Rascunho salvo"

            if not salvamento.get("ok"):
                resultado["status_erro"] = "erro_salvamento"
                resultado["mensagem"] = f"Falha ao {'publicar' if publicar_direto else 'salvar rascunho'}"
                resultado["screenshot_path"] = await self._tirar_screenshot("erro_salvamento")
                return resultado

            resultado["sucesso"] = True
            resultado["mensagem"] = f"{modo} com sucesso"
            resultado["mercadoi_url"] = salvamento.get("url", "")
            return resultado

        except Exception as e:
            logger.error(f"Erro no driver Mercadoi: {e}")
            resultado["status_erro"] = "erro_preenchimento"
            resultado["mensagem"] = str(e)
            resultado["screenshot_path"] = await self._tirar_screenshot("erro_preenchimento")
            return resultado

    def _normalizar_tipo_imovel(self, valor: str) -> str:
        texto = normalizar(valor or "")
        if "cobertura" in texto:
            return "Apto. Cobertura"
        if "flat" in texto:
            return "Flat"
        if "apart" in texto or "studio" in texto or "kitnet" in texto or "kit net" in texto:
            return "Apartamento"
        if "chacara" in texto:
            return "Chácara"
        if "fazenda" in texto:
            return "Fazenda"
        if "sitio" in texto:
            return "Sítio"
        if "casa" in texto or "resid" in texto or "sobrado" in texto:
            return "Casa"
        if "terreno" in texto or "lote" in texto:
            return "Terreno"
        if "sala" in texto or "comercial" in texto or "loja" in texto or "escritorio" in texto:
            return "Sala Comercial"
        return valor.strip() or "Apartamento"

    async def _tirar_screenshot(self, etapa: str) -> str:
        """Captura screenshot da página atual e salva em logs/screenshots/."""
        try:
            os.makedirs("logs/screenshots", exist_ok=True)
            prefixo = f"{self.execution_id}_" if self.execution_id else ""
            caminho = os.path.join("logs", "screenshots", f"{prefixo}{etapa}.png")
            await self._page.screenshot(path=caminho, full_page=True)
            logger.info(f"Screenshot salvo: {caminho}")
            return caminho
        except Exception as e:
            logger.warning(f"Não foi possível salvar screenshot: {e}")
            return ""

    async def _selecionar_por_texto(self, page, seletor, valor):
        """
        Seleciona uma opcao de <select> pelo texto, usando JavaScript direto.
        Funciona tanto para selects normais quanto para selects controlados por select2,
        pois manipula o valor no DOM e dispara os eventos necessarios.
        """
        try:
            valor_norm = normalizar(valor)
            resultado = await page.evaluate(r"""
                ({seletor, valorNorm}) => {
                    const sel = document.querySelector(seletor);
                    if (!sel) return { ok: false, motivo: 'elemento nao encontrado' };
                    let melhorOpcao = null;
                    let melhorTexto = '';
                    for (const op of sel.options) {
                        const textoNorm = op.text.toLowerCase()
                            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                            .replace(/[ \\t]+/g, ' ').trim();
                        if (!textoNorm) continue;
                        // Strip prefixo "N- " (ex: "6- Usado" → "usado") para matching robusto
                        const textoBase = textoNorm.replace(/^\d+\s*[-\u2013]\s*/, '');
                        const match = textoNorm === valorNorm || textoNorm.includes(valorNorm) || valorNorm.includes(textoNorm)
                                   || textoBase === valorNorm || textoBase.includes(valorNorm) || valorNorm.includes(textoBase);
                        if (match) {
                            if (!melhorOpcao || op.text.length < melhorOpcao.text.length) {
                                melhorOpcao = op;
                                melhorTexto = op.text;
                            }
                        }
                    }
                    if (!melhorOpcao) return { ok: false, motivo: 'valor nao encontrado nas opcoes' };
                    if (window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).val(melhorOpcao.value).trigger('change');
                    } else {
                        sel.value = melhorOpcao.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        sel.dispatchEvent(new Event('input',  { bubbles: true }));
                    }
                    return { ok: true, texto: melhorTexto };
                }
            """, {"seletor": seletor, "valorNorm": valor_norm})

            if resultado and resultado.get('ok'):
                logger.info(f"Selecionado '{resultado['texto']}' em {seletor}")
                return True
            else:
                motivo = resultado.get('motivo', 'desconhecido') if resultado else 'erro JS'
                logger.info(f"Nao selecionou '{valor}' em {seletor}: {motivo}")
                return False
        except Exception as e:
            logger.info(f"Erro ao selecionar '{valor}' em {seletor}: {e}")
            return False

    async def _selecionar_tipo_imovel(self, page, tipo_imovel):
        """
        Seleciona o tipo principal por value fixo quando conhecido.
        O Mercadoi usa categorias numericas e, em alguns carregamentos, o select2
        nao atualiza bem quando escolhemos apenas por texto.
        """
        mapa_values = {
            "Apartamento": "16",
            "Casa": "53",
            "Terreno": "103",
            "Sala Comercial": "98",
            # Flat, Apto. Cobertura, Chácara, Fazenda, Sítio — usa matching por texto abaixo
        }
        value = mapa_values.get(tipo_imovel)
        if value:
            try:
                resultado = await page.evaluate("""
                    ({value}) => {
                        const sel = document.querySelector('#prop_type');
                        if (!sel) return {ok: false, motivo: 'elemento nao encontrado'};
                        const option = Array.from(sel.options).find(op => op.value === value);
                        if (!option) return {ok: false, motivo: 'value nao encontrado'};
                        sel.value = value;
                        sel.dispatchEvent(new Event('input', {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        if (window.jQuery) {
                            window.jQuery(sel).val(value).trigger('change');
                            window.jQuery(sel).trigger({
                                type: 'select2:select',
                                params: {data: {id: value, text: option.text}}
                            });
                        }
                        return {ok: true, texto: option.text};
                    }
                """, {"value": value})
                if resultado and resultado.get("ok"):
                    logger.info(f"Selecionado tipo de imovel '{resultado['texto']}' em #prop_type")
                    return True
                motivo = resultado.get("motivo", "desconhecido") if resultado else "erro JS"
                logger.warning(f"Nao selecionou tipo '{tipo_imovel}' por value: {motivo}")
            except Exception as e:
                logger.warning(f"Erro ao selecionar tipo '{tipo_imovel}' por value: {e}")

        return await self._selecionar_por_texto(page, '#prop_type', tipo_imovel)

    async def _marcar_nao_exibir_contato(self, page):
        """
        Marca a opcao 'Nao exibir contato' via JavaScript.
        Ha 3 radios fave_agent_display_option; o de value='2' ou o ultimo = Nao exibir.
        """
        try:
            marcado = await page.evaluate("""
                () => {
                    const radios = document.querySelectorAll('input[name="fave_agent_display_option"]');
                    if (!radios.length) return false;
                    let alvo = null;
                    for (const r of radios) {
                        if (r.value === "2") { alvo = r; break; }
                    }
                    if (!alvo) alvo = radios[radios.length - 1];
                    alvo.checked = true;
                    alvo.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            """)
            if marcado:
                logger.info("Marcado: Nao exibir contato (via JS)")
            else:
                logger.warning("Input 'nao exibir contato' nao encontrado na pagina")
        except Exception as e:
            logger.warning(f"Erro ao marcar nao exibir contato: {e}")

    def _montar_conteudo(self, dados):
        descricao = dados.get("descricao_util", "")
        url_pub   = self._normalizar_url(dados.get("url_publicacao", ""))
        whatsapp  = self._normalizar_url(dados.get("whatsapp_url", ""))
        instagram = self._normalizar_url(dados.get("instagram_url", ""))

        icones = []
        if url_pub:
            icones.append(
                f'<a href="{url_pub}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/ver-video-mi/" width="120" height="120" '
                f'data-src="https://mercadoi.com.br/ver-video-mi/" /></a>'
            )
        if whatsapp:
            icones.append(
                f'<a href="{whatsapp}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/whatsapp-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/whatsapp-mi/" /></a>'
            )
        if instagram:
            icones.append(
                f'<a href="{instagram}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/instagram-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/instagram-mi/" /></a>'
            )

        if icones:
            logger.info(f"Ícones de contato inseridos: "
                        f"{'Ver Imóvel ' if url_pub else ''}"
                        f"{'WhatsApp ' if whatsapp else ''}"
                        f"{'Instagram' if instagram else ''}")
        bloco_html = ("\n\n<pre>" + "".join(icones) + "</pre>") if icones else ""
        return descricao + bloco_html

    @staticmethod
    def _normalizar_url(v: str) -> str:
        """Normaliza e valida uma URL. Aceita wa.me sem prefixo, retorna '' se inválida."""
        v = v.strip()
        if not v:
            return ""
        # Adiciona https:// quando ausente em wa.me e instagram.com
        if v.startswith("wa.me/") or v.startswith("instagram.com/"):
            v = "https://" + v
        if v.startswith("http://") or v.startswith("https://"):
            return v
        return ""

    async def _preencher_editor(self, page, conteudo):
        try:
            conteudo_html = conteudo.replace("\n", "<br>")

            # Guarda o conteúdo em variável JS para o AJAX usar com segurança,
            # independente do estado do TinyMCE
            await page.evaluate(
                f"() => {{ window._botDescricao = {json.dumps(conteudo_html)}; }}"
            )

            # Aguarda TinyMCE inicializar
            try:
                await page.wait_for_function(
                    "() => { const ed = window.tinymce || window.tinyMCE; return !!(ed && ed.get('prop_des')); }",
                    timeout=3000
                )
            except Exception:
                pass

            resultado = await page.evaluate(f"""
                () => {{
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    if (editor) {{
                        editor.setContent({json.dumps(conteudo_html)});
                        editor.fire('change');
                        editor.fire('input');
                        editor.save();
                        return 'tinymce';
                    }}
                    const ta = document.querySelector('#prop_des');
                    if (ta) {{
                        ta.value = {json.dumps(conteudo_html)};
                        ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                        ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return 'textarea';
                    }}
                    return null;
                }}
            """)
            if resultado:
                logger.info(f"Conteudo preenchido via {resultado}")
            else:
                logger.warning("Editor de conteudo nao encontrado")
        except Exception as e:
            logger.warning(f"Erro ao preencher editor: {e}")

    async def _preencher_campos_batch(self, page, campos: dict):
        """Preenche múltiplos inputs em uma única chamada JS."""
        try:
            preenchidos = await page.evaluate("""
                (campos) => {
                    const log = [];
                    for (const [sel, val] of Object.entries(campos)) {
                        if (!val) continue;
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        el.value = val;
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        log.push(sel + '=' + val);
                    }
                    return log;
                }
            """, campos)
            if preenchidos:
                logger.info(f"Campos preenchidos em batch: {', '.join(preenchidos)}")
        except Exception as e:
            logger.warning(f"Erro ao preencher campos em batch: {e}")

    async def _selecionar_batch(self, page, selecoes: list):
        """Seleciona múltiplos <select> em uma única chamada JS."""
        try:
            validos = [(s, v) for s, v in selecoes if v]
            if not validos:
                return
            resultado = await page.evaluate("""
                (selecoes) => {
                    const log = [];
                    for (const [seletor, valorAlvo] of selecoes) {
                        const sel = document.querySelector(seletor);
                        if (!sel) continue;
                        const norm = t => t.toLowerCase()
                            .normalize('NFD').replace(/[\\u0300-\\u036f]/g,'')
                            .replace(/\\s+/g,' ').trim();
                        const alvo = norm(valorAlvo);
                        let melhor = null;
                        for (const op of sel.options) {
                            const t = norm(op.text);
                            const b = t.replace(/^\\d+\\s*[-\\u2013]\\s*/, '');
                            if (t === alvo || b === alvo || t.includes(alvo) || alvo.includes(b)) {
                                melhor = op; break;
                            }
                        }
                        if (melhor) {
                            if (window.jQuery && window.jQuery(sel).data('select2')) {
                                window.jQuery(sel).val(melhor.value).trigger('change');
                            } else {
                                sel.value = melhor.value;
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                sel.dispatchEvent(new Event('input',  {bubbles: true}));
                            }
                            log.push(seletor + '=' + melhor.text);
                        }
                    }
                    return log;
                }
            """, validos)
            if resultado:
                logger.info(f"Selects em batch: {len(resultado)} selecionados")
        except Exception as e:
            logger.warning(f"Erro ao selecionar em batch: {e}")

    def _cidade_por_bairro(self, bairro: str) -> str:
        b = normalizar(bairro)
        # Busca exata primeiro, depois substring
        if b in _BAIRRO_CIDADE_PB:
            return _BAIRRO_CIDADE_PB[b]
        for key, cidade in _BAIRRO_CIDADE_PB.items():
            if key in b or b in key:
                return cidade
        return ""

    async def _selecionar_cidade(self, page, cidade, bairro=""):
        if cidade:
            ok = await self._selecionar_por_texto(page, '#city', cidade)
            if ok:
                await self._aguardar_opcoes_bairro(page)
                return cidade
        # Tenta inferir cidade pelo bairro
        if bairro:
            cidade_inferida = self._cidade_por_bairro(bairro)
            if cidade_inferida:
                ok = await self._selecionar_por_texto(page, '#city', cidade_inferida)
                if ok:
                    logger.info(f"Cidade inferida pelo bairro '{bairro}': {cidade_inferida}")
                    await self._aguardar_opcoes_bairro(page)
                    return cidade_inferida
        logger.info(f"Cidade '{cidade}' nao encontrada, usando Joao Pessoa")
        await self._selecionar_por_texto(page, '#city', "João Pessoa")
        await self._aguardar_opcoes_bairro(page)
        return "Joao Pessoa"

    async def _aguardar_opcoes_bairro(self, page, timeout_s: int = 6):
        """Aguarda o select #neighborhood ser populado via AJAX após seleção da cidade."""
        try:
            await page.wait_for_function(
                "() => { const s = document.querySelector('#neighborhood'); return s && s.options.length > 1; }",
                timeout=timeout_s * 1000
            )
        except Exception:
            logger.warning("Timeout aguardando opções de bairro")

    async def _selecionar_bairro(self, page, bairro):
        if not bairro:
            return ""

        # Tenta o nome completo primeiro
        if await self._selecionar_por_texto(page, '#neighborhood', bairro):
            return bairro

        # Tenta apenas a primeira palavra (ex: "Intermares Sul" → "Intermares")
        primeira_palavra = bairro.split()[0] if bairro.split() else ""
        if primeira_palavra and primeira_palavra != bairro:
            if await self._selecionar_por_texto(page, '#neighborhood', primeira_palavra):
                logger.info(f"Bairro parcial aceito: '{primeira_palavra}' (original: '{bairro}')")
                return primeira_palavra

        logger.info(f"Bairro '{bairro}' nao encontrado no select — campo deixado em branco")
        return ""

    def _validar_arquivos(self, caminhos: list) -> list:
        """Valida arquivos e converte formatos não suportados antes do upload.

        JPEG e PNG são aceitos diretamente pelo Mercadoi.
        WEBP, BMP, TIFF, GIF e outros são convertidos para JPEG.
        """
        from PIL import Image as _PIL
        FORMATOS_OK = {"JPEG", "PNG"}
        EXTENSOES_OK = {".jpg", ".jpeg", ".png"}
        validos = []
        for c in caminhos:
            if not os.path.exists(c) or os.path.getsize(c) == 0:
                logger.warning(f"Arquivo ausente ou vazio, ignorado: {c}")
                continue
            try:
                with _PIL.open(c) as img:
                    fmt = img.format
                    mode = img.mode

                ext = os.path.splitext(c)[1].lower()
                formato_suportado = fmt in FORMATOS_OK and ext in EXTENSOES_OK

                if formato_suportado:
                    validos.append(c)
                else:
                    # Converte WEBP, BMP, TIFF, GIF, etc. para JPEG
                    novo = os.path.splitext(c)[0] + "_conv.jpg"
                    with _PIL.open(c) as img:
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        img.save(novo, "JPEG", quality=92, optimize=True)
                    logger.info(f"Convertido para JPEG: {os.path.basename(c)} (era {fmt}/{mode}) → {os.path.basename(novo)}")
                    validos.append(novo)
            except Exception as e:
                logger.warning(f"Imagem inválida/corrompida, ignorada: {c} — {e}")
        return validos

    async def _anexar_midia(self, page, caminhos):
        """Anexa uma ou mais imagens à galeria via plupload."""
        if isinstance(caminhos, str):
            caminhos = [caminhos]
        if not caminhos:
            return False
        caminhos = self._validar_arquivos(caminhos)
        if not caminhos:
            logger.error("Nenhum arquivo válido para upload após validação")
            return False
        try:
            esperados = len(caminhos)
            enviado = False
            upload_btn = None
            for sel in ['#select_gallery_images', '#plupload-browse-button', 'a.plupload_add', '.plupload_add',
                        'a[id*="browse"]', 'button[id*="browse"]', 'div[id*="browse"]',
                        'a[id*="select"]', 'a[id*="upload"]', 'a[id*="gallery"]']:
                upload_btn = await page.query_selector(sel)
                if upload_btn:
                    logger.info(f"Usando botao de upload: {sel}")
                    break

            if upload_btn:
                try:
                    async with page.expect_file_chooser(timeout=5000) as fc_info:
                        await upload_btn.click()
                    fc = await fc_info.value
                    await fc.set_files(caminhos)
                    enviado = True
                    logger.info(f"{esperados} arquivo(s) enviado(s) via file_chooser")
                except Exception as e:
                    logger.warning(f"File chooser nao abriu, tentando input file direto: {e}")

            if not enviado:
                input_file = await self._localizar_input_upload(page)
                if input_file:
                    await input_file.set_input_files(caminhos)
                    enviado = True
                    logger.info(f"{esperados} arquivo(s) enviado(s) via input file")
                else:
                    logger.warning("Campo de upload nao encontrado")
                    return False

            # Aguarda upload iniciar no servidor
            await page.wait_for_timeout(500)

            # Detecta erros do plupload exibidos na página
            erro_plupload = await page.evaluate("""
                () => {
                    const errs = document.querySelectorAll(
                        '.plupload_error, .moxie-shim-error, [class*="error"][class*="upload"], .plupload .error'
                    );
                    return Array.from(errs).map(e => e.innerText).filter(t => t).join(' | ');
                }
            """)
            if erro_plupload:
                logger.warning(f"Erro de upload detectado na página: {erro_plupload}")

            ids = []
            limite = max(esperados * 8, 20)  # mais iterações, mas cada uma mais curta
            for i in range(limite):
                await page.wait_for_timeout(800)

                # Verifica campos ocultos (Mercadoi usa "propperty" com pp duplo, mas também testa variante)
                ids = await page.evaluate("""
                    () => {
                        const sels = [
                            'input[name="propperty_image_ids[]"]',
                            'input[name="property_image_ids[]"]',
                            'input[name*="image_ids"]',
                        ];
                        for (const s of sels) {
                            const found = Array.from(document.querySelectorAll(s))
                                .map(el => el.value).filter(v => v && v !== '0');
                            if (found.length) return found;
                        }
                        return [];
                    }
                """)

                # Verifica thumbnails visíveis na galeria como confirmação alternativa
                thumbs = await page.evaluate("""
                    () => {
                        const sels = [
                            '.fave_property_images .preview-item',
                            '.fave_property_images img',
                            '.property-gallery-upload img',
                            'ul.fave_images_list li',
                            '[class*="gallery"] .thumbnail',
                            '[class*="upload"] img[src*="uploads"]',
                        ];
                        for (const s of sels) {
                            const n = document.querySelectorAll(s).length;
                            if (n > 0) return n;
                        }
                        return 0;
                    }
                """)

                confirmados = max(len(ids), thumbs if isinstance(thumbs, int) else 0)
                logger.info(f"Uploads concluidos: {confirmados}/{esperados} (ids={len(ids)}, thumbs={thumbs})")

                if len(ids) >= esperados:
                    logger.info("Todos os arquivos confirmados via IDs")
                    return True
                if isinstance(thumbs, int) and thumbs >= esperados:
                    logger.info(f"Todos os arquivos confirmados via thumbnails ({thumbs})")
                    return True

            # Aceita parcial se ao menos 1 foi confirmado
            confirmados_final = max(len(ids), thumbs if isinstance(thumbs, int) else 0)
            if confirmados_final > 0:
                logger.warning(f"Upload parcial aceito: {confirmados_final}/{esperados} arquivo(s)")
                return True

            logger.error(f"Upload falhou: nenhuma imagem confirmada no servidor ({esperados} esperado(s))")
            return False
        except Exception as e:
            logger.error(f"Erro ao anexar midia: {e}")
            return False

    async def _localizar_input_upload(self, page):
        try:
            handles = await page.query_selector_all('input[type="file"]')
            for handle in handles:
                try:
                    if await handle.is_visible():
                        return handle
                except Exception:
                    continue
            return handles[0] if handles else None
        except Exception:
            return None

    async def _salvar_rascunho(self, page):
        try:
            try:
                await page.wait_for_selector('#save_as_draft', timeout=10000)
            except Exception:
                logger.warning("Timeout aguardando #save_as_draft, tentando mesmo assim")

            # Replicar o AJAX do handler #save_as_draft diretamente.
            # O handler original usa tinyMCE.get('prop_des').getContent(), mas tinyMCE nao
            # inicializa o editor neste contexto — usamos o valor do textarea diretamente.
            resultado = await page.evaluate("""
                () => new Promise((resolve) => {
                    const $form = jQuery('#submit_property_form');
                    if (!$form.length) { resolve({ok: false, erro: 'form nao encontrado'}); return; }
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    const description = editor
                        ? editor.getContent()
                        : (window._botDescricao || (document.querySelector('#prop_des') || {}).value || '');
                    const ajaxUrl = window.ajax_url || window.ajaxurl || '/wp-admin/admin-ajax.php';
                    jQuery.ajax({
                        type: 'post',
                        url: ajaxUrl,
                        dataType: 'json',
                        data: $form.serialize() + '&action=save_as_draft&description=' + encodeURIComponent(description),
                        success: function(response) { resolve({ok: true, response: JSON.stringify(response)}); },
                        error: function(xhr, status, error) {
                            resolve({ok: false, erro: error, status: status, resp: xhr.responseText.substring(0, 300)});
                        }
                    });
                })
            """)

            logger.info(f"Resultado AJAX save_as_draft: {resultado}")

            if not resultado or not resultado.get('ok'):
                logger.error(f"AJAX falhou: {resultado}")
                return {"ok": False}

            try:
                resp = json.loads(resultado.get('response', '{}'))
                if resp.get('success') or resp.get('suc'):
                    logger.info("Rascunho salvo com sucesso via AJAX")
                    property_id = resp.get("property_id") or resp.get("prop_id") or resp.get("id")
                    url = self._montar_url_mercadoi(property_id)
                    return {"ok": True, "property_id": property_id, "url": url}
                else:
                    logger.error(f"AJAX retornou falha: {resp}")
                    return {"ok": False}
            except Exception:
                logger.info("AJAX completou (resposta aceita)")
                return {"ok": True}

        except Exception as e:
            logger.error(f"Erro ao salvar rascunho: {e}")
            return {"ok": False}

    async def _publicar(self, page):
        """Publica o imóvel diretamente (sem rascunho) via AJAX submit_property."""
        try:
            try:
                await page.wait_for_selector('#save_as_draft', timeout=10000)
            except Exception:
                logger.warning("Timeout aguardando form, tentando publicar mesmo assim")

            resultado = await page.evaluate("""
                () => new Promise((resolve) => {
                    const $form = jQuery('#submit_property_form');
                    if (!$form.length) { resolve({ok: false, erro: 'form nao encontrado'}); return; }
                    const ed = window.tinymce || window.tinyMCE;
                    const editor = ed && ed.get('prop_des');
                    const description = editor
                        ? editor.getContent()
                        : (window._botDescricao || (document.querySelector('#prop_des') || {}).value || '');
                    const ajaxUrl = window.ajax_url || window.ajaxurl || '/wp-admin/admin-ajax.php';
                    jQuery.ajax({
                        type: 'post',
                        url: ajaxUrl,
                        dataType: 'json',
                        data: $form.serialize() + '&action=submit_property&description=' + encodeURIComponent(description),
                        success: function(response) { resolve({ok: true, response: JSON.stringify(response)}); },
                        error: function(xhr, status, error) {
                            resolve({ok: false, erro: error, status: status, resp: xhr.responseText.substring(0, 300)});
                        }
                    });
                })
            """)

            logger.info(f"Resultado AJAX submit_property: {resultado}")

            if not resultado or not resultado.get('ok'):
                # Fallback: tenta clicar no botão de submit do formulário
                logger.warning("AJAX submit_property falhou, tentando clique no botão")
                return await self._publicar_via_botao(page)

            try:
                resp = json.loads(resultado.get('response', '{}'))
                if resp.get('success') or resp.get('suc'):
                    logger.info("Imóvel publicado com sucesso via AJAX")
                    property_id = resp.get("property_id") or resp.get("prop_id") or resp.get("id")
                    url = self._montar_url_mercadoi(property_id)
                    return {"ok": True, "property_id": property_id, "url": url}
                else:
                    logger.warning(f"AJAX submit_property retornou falha: {resp} — tentando botão")
                    return await self._publicar_via_botao(page)
            except Exception:
                logger.info("AJAX publicar completou (resposta aceita)")
                return {"ok": True}

        except Exception as e:
            logger.error(f"Erro ao publicar: {e}")
            return {"ok": False}

    async def _publicar_via_botao(self, page):
        """Fallback: clica no botão de submit do formulário para publicar."""
        try:
            seletores = [
                '#add_new_property',
                'button:has-text("Enviar imóvel")',
                'button:has-text("Enviar imovel")',
                'input[type="submit"][value*="Enviar"]',
                'button[type="submit"][id*="submit"]',
                'input[type="submit"][id*="submit"]',
                '#submit-property',
                'button:has-text("Publicar")',
                'button:has-text("Submit")',
                'button:has-text("Enviar")',
            ]
            for sel in seletores:
                btn = await page.query_selector(sel)
                if btn:
                    url_antes = page.url
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    url_depois = page.url

                    # URL igual = formulário ficou na mesma página (erro de validação)
                    if url_depois == url_antes:
                        logger.warning(
                            f"Botão '{sel}' clicado mas página não redirecionou "
                            f"— erro de validação do formulário (campo obrigatório faltando?)"
                        )
                        return {"ok": False}

                    logger.info(f"Publicado via clique no botão: {sel}")
                    import re as _re
                    m = _re.search(r'post=(\d+)', url_depois)
                    pid = m.group(1) if m else None
                    return {"ok": True, "property_id": pid, "url": self._montar_url_mercadoi(pid)}
            logger.error("Nenhum botão de publicar encontrado")
            return {"ok": False}
        except Exception as e:
            logger.error(f"Erro ao publicar via botão: {e}")
            return {"ok": False}

    def _montar_url_mercadoi(self, property_id) -> str:
        if not property_id:
            return ""
        return urljoin(self.base_url.rstrip("/") + "/", f"wp-admin/post.php?post={property_id}&action=edit")
