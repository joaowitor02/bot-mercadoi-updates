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
            await page.wait_for_timeout(1000)

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

            # PRECO
            preco = dados.get("preco", "").strip()
            if preco:
                await page.fill('#prop_price', preco)
                logger.info(f"Preco preenchido: {preco}")

            # ESTAGIO
            estagio = dados.get("estagio_imovel", "").strip()
            if estagio:
                await self._selecionar_por_texto(page, 'select[name="estagio-da-obra-imc3b3vel[]"]', estagio)

            # ANDAR
            andar = dados.get("andar", "").strip()
            if andar:
                await self._selecionar_por_texto(page, 'select[name="no-tc3a9rreo[]"]', andar)

            # ELEVADOR
            elevador = dados.get("elevador", "").strip()
            if elevador:
                await self._selecionar_por_texto(page, 'select[name="tem-elevador"]', elevador)

            # QUARTOS
            quartos = dados.get("quartos", "").strip()
            if quartos:
                await page.fill('#prop_beds', quartos)
                logger.info(f"Quartos preenchido: {quartos}")

            # SUITES
            suites = dados.get("suites", "").strip()
            if suites:
                await page.fill('#prop_rooms', suites)
                logger.info(f"Suites preenchido: {suites}")

            # BANHEIROS
            banheiros = dados.get("banheiros", "").strip()
            if banheiros:
                await page.fill('#prop_baths', banheiros)
                logger.info(f"Banheiros preenchido: {banheiros}")

            # VAGAS
            vagas = dados.get("vagas", "").strip()
            if vagas:
                await page.fill('#prop_garage', vagas)
                logger.info(f"Vagas preenchido: {vagas}")

            # TAMANHO
            area = dados.get("area_m2", "").strip()
            if area:
                await page.fill('#prop_size', area)
                logger.info(f"Area preenchida: {area}")

            # ROLE (tipo de conta)
            await self._selecionar_por_texto(page, 'select[name="role"]', "Corretor")

            # FAZ PARCERIA - sempre "A combinar"
            await self._selecionar_por_texto(page, 'select[name="faz-parcerc3ada"]', "A combinar")

            # CIDADE
            cidade = dados.get("cidade_extraida", "").strip()
            cidade_aplicada = await self._selecionar_cidade(page, cidade)
            resultado["cidade_aplicada"] = cidade_aplicada

            # BAIRRO
            bairro = dados.get("bairro_extraido", "").strip()
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

            # SALVAR COMO RASCUNHO
            salvamento = await self._salvar_rascunho(page)
            if not salvamento.get("ok"):
                resultado["status_erro"] = "erro_salvamento"
                resultado["mensagem"] = "Falha ao salvar rascunho"
                resultado["screenshot_path"] = await self._tirar_screenshot("erro_salvamento")
                return resultado

            resultado["sucesso"] = True
            resultado["mensagem"] = "Rascunho salvo com sucesso"
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
        if "apart" in texto or "flat" in texto:
            return "Apartamento"
        if "casa" in texto or "resid" in texto:
            return "Casa"
        if "terreno" in texto or "lote" in texto:
            return "Terreno"
        if "sala" in texto or "comercial" in texto or "loja" in texto:
            return "Sala Comercial"
        if "studio" in texto or "kitnet" in texto:
            return "Apartamento"
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
                    sel.value = melhorOpcao.value;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    if (window.jQuery && window.jQuery(sel).data('select2')) {
                        window.jQuery(sel).trigger('change');
                    }
                    return { ok: true, texto: melhorTexto };
                }
            """, {"seletor": seletor, "valorNorm": valor_norm})

            if resultado and resultado.get('ok'):
                logger.info(f"Selecionado '{resultado['texto']}' em {seletor}")
                return True
            else:
                motivo = resultado.get('motivo', 'desconhecido') if resultado else 'erro JS'
                logger.warning(f"Nao selecionou '{valor}' em {seletor}: {motivo}")
                return False
        except Exception as e:
            logger.warning(f"Erro ao selecionar '{valor}' em {seletor}: {e}")
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
        url_pub = dados.get("url_publicacao", "").strip()
        whatsapp = dados.get("whatsapp_url", "").strip()
        instagram = dados.get("instagram_url", "").strip()

        def url_valida(v):
            return v.startswith("http://") or v.startswith("https://")

        icones = []
        if url_pub and url_valida(url_pub):
            icones.append(
                f'<a href="{url_pub}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/ver-video-mi/" width="120" height="120" '
                f'data-src="https://mercadoi.com.br/ver-video-mi/" /></a>'
            )
        if whatsapp and url_valida(whatsapp):
            icones.append(
                f'<a href="{whatsapp}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/whatsapp-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/whatsapp-mi/" /></a>'
            )
        if instagram and url_valida(instagram):
            icones.append(
                f'<a href="{instagram}" target="_blank" rel="noopener">'
                f'<img class="" src="https://mercadoi.com.br/instagram-mi/" width="75" height="75" '
                f'data-src="https://mercadoi.com.br/instagram-mi/" /></a>'
            )

        bloco_html = ("\n\n<pre>" + "".join(icones) + "</pre>") if icones else ""
        return descricao + bloco_html

    async def _preencher_editor(self, page, conteudo):
        try:
            conteudo_html = conteudo.replace("\n", "<br>")

            # Guarda o conteúdo em variável JS para o AJAX usar com segurança,
            # independente do estado do TinyMCE
            await page.evaluate(
                f"() => {{ window._botDescricao = {json.dumps(conteudo_html)}; }}"
            )

            # Aguarda TinyMCE inicializar (até 4s)
            for _ in range(8):
                pronto = await page.evaluate("""
                    () => {
                        const ed = window.tinymce || window.tinyMCE;
                        return !!(ed && ed.get('prop_des'));
                    }
                """)
                if pronto:
                    break
                await page.wait_for_timeout(500)

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

    async def _selecionar_cidade(self, page, cidade):
        if cidade:
            ok = await self._selecionar_por_texto(page, '#city', cidade)
            if ok:
                await self._aguardar_opcoes_bairro(page)
                return cidade
        logger.info(f"Cidade '{cidade}' nao encontrada, usando Joao Pessoa")
        await self._selecionar_por_texto(page, '#city', "João Pessoa")
        await self._aguardar_opcoes_bairro(page)
        return "Joao Pessoa"

    async def _aguardar_opcoes_bairro(self, page, timeout_s: int = 6):
        """Aguarda o select #neighborhood ser populado via AJAX após seleção da cidade."""
        for _ in range(timeout_s * 2):
            n = await page.evaluate(
                "() => { const s = document.querySelector('#neighborhood'); return s ? s.options.length : 0; }"
            )
            if n > 1:
                return
            await page.wait_for_timeout(500)
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

        logger.warning(f"Bairro '{bairro}' nao encontrado no select — campo deixado em branco")
        return ""

    async def _anexar_midia(self, page, caminhos):
        """Anexa uma ou mais imagens à galeria via plupload."""
        if isinstance(caminhos, str):
            caminhos = [caminhos]
        if not caminhos:
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

            ids = []
            limite = max(esperados * 8, 20)
            for _ in range(limite):
                await page.wait_for_timeout(1500)
                ids = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('input[name="propperty_image_ids[]"]'))
                        .map(el => el.value).filter(v => v && v !== '0')
                """)
                logger.info(f"Uploads concluidos: {len(ids)}/{esperados}")
                if len(ids) >= esperados:
                    preview = await page.query_selector('.fotorama__img, .moxie-shim img, .thumbnail img, [class*="preview"] img, [class*="gallery"] img, .attachment-thumbnail')
                    logger.info("Preview de imagem detectado" if preview else "Preview de imagem nao detectado")
                    return True

            logger.error(f"Upload incompleto no Mercadoi: {len(ids)}/{esperados} arquivo(s)")
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

    def _montar_url_mercadoi(self, property_id) -> str:
        if not property_id:
            return ""
        return urljoin(self.base_url.rstrip("/") + "/", f"?p={property_id}")
