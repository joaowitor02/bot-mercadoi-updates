"""
Automacao de cadastro de anuncios imobiliarios no Mercadoi
Versao: 4.0
"""

import argparse
import asyncio
import sys
import json
import os
import re
import uuid
import time
import httpx
from version import VERSION
from modules.database_manager import DatabaseManager
from modules.instagram_scraper import InstagramScraper
from modules.instagram_chrome_scraper import extrair_via_chrome
from modules.deepseek_api import DeepSeekAPIClient
from modules.deepseek_client import DeepSeekClient
from modules.deepseek_parser import DeepSeekParser
from modules.media_resolver import MediaResolver
from modules.mercadoi_driver import MercadoiDriver
from modules.cep_lookup import buscar_cep
from modules.wordpress_publisher import WordPressPublisher
from modules.wordpress_xmlrpc_publisher import WordPressXmlRpcPublisher
from modules.status_writer import StatusWriter
from modules.logger import Logger
from modules.notificador import notificar
from modules.ocr_preco import extrair_preco_de_imagens
from modules.olx_scraper import OlxScraper, normalizar_url as normalizar_olx_url, url_valida as olx_url_valida
from modules.orulo_scraper import OruloScraper, normalizar_url as normalizar_orulo_url, url_valida as orulo_url_valida
from modules.property_types import aplicar_tipos_imovel

logger = Logger("main")

MAX_TENTATIVAS_MERCADOI = 3
ESPERA_ENTRE_TENTATIVAS = 5  # segundos
SALVAR_TUDO_COMO_RASCUNHO = True
MEDIA_DOWNLOAD_TIMEOUT = 180  # cancela download se exceder — evita travar em vídeos grandes


def _normalizar_whatsapp_url(valor: str) -> str:
    valor = str(valor or "").strip()
    if not valor:
        return ""
    if valor.lower().startswith(("http://", "https://")):
        return valor
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) in (10, 11):
        digitos = f"55{digitos}"
    if digitos.startswith("55") and len(digitos) in (12, 13):
        return f"https://wa.me/{digitos}"
    return ""


def _usar_wordpress_api(config: dict) -> bool:
    """Retorna True quando o plugin REST API está configurado e ativado."""
    return (
        bool(config.get("usar_wordpress_api"))
        and bool(config.get("wordpress_api_url", "").strip())
        and bool(config.get("wordpress_api_key", "").strip())
    )


def _usar_wordpress_xmlrpc(config: dict) -> bool:
    """Retorna True quando XML-RPC está configurado (não exige plugin no site do cliente)."""
    return (
        bool(config.get("usar_wordpress_api"))
        and bool(config.get("wordpress_xmlrpc_url", "").strip())
        and bool(config.get("wordpress_xmlrpc_user", "").strip())
        and bool(config.get("wordpress_xmlrpc_password", "").strip())
    )


def _forcar_fluxo_browser_mercadoi(config: dict) -> bool:
    """Mantem o fluxo historico pelo formulario real do Mercadoi por padrao."""
    return bool(config.get("forcar_fluxo_browser_mercadoi", True))


# ---------------------------------------------------------------------------
# Validação dos dados extraídos pela IA
# ---------------------------------------------------------------------------

def _validar_dados(dados: dict) -> dict:
    """
    Sanitiza campos numéricos para corrigir valores absurdos da IA.
    Regras baseadas em limites do mundo real para imóveis residenciais.
    """
    import re as _re

    def to_int(v):
        try:
            return int(str(v).strip())
        except Exception:
            return None

    # Normaliza "0" (ou "0.0", "00" etc.) → "" para campos numéricos.
    # A IA às vezes retorna 0 quando o campo não existe em vez de deixar vazio.
    _CAMPOS_ZERO = ["quartos", "suites", "banheiros", "vagas",
                    "area_m2", "area_terreno", "preco", "condominio", "iptu",
                    "taxas", "ano_construcao"]
    for campo in _CAMPOS_ZERO:
        v = dados.get(campo)
        if isinstance(v, str) and _re.match(r'^\s*0+\s*$', v):
            dados[campo] = ""
        elif isinstance(v, (int, float)) and v == 0:
            dados[campo] = ""

    # Valida preço: mais de 8 dígitos → telefone ou CRECI, não preço
    preco_raw = dados.get("preco", "")
    if isinstance(preco_raw, str):
        apenas_digitos = _re.sub(r'[^\d]', '', preco_raw)
        if len(apenas_digitos) > 8:
            logger.warning(f"Preço suspeito ({preco_raw!r}) — descartado (muitos dígitos, provável telefone ou CRM)")
            dados["preco"] = ""

    quartos = to_int(dados.get("quartos"))
    suites = to_int(dados.get("suites"))
    banheiros = to_int(dados.get("banheiros"))
    vagas = to_int(dados.get("vagas"))
    area = to_int(dados.get("area_m2"))

    # suites nunca pode ser maior que quartos
    if quartos is not None and suites is not None and suites > quartos:
        corrigido = quartos // 2 if quartos > 1 else 0
        logger.warning(f"Suites ({suites}) > quartos ({quartos}) — corrigindo para {corrigido or ''}")
        dados["suites"] = str(corrigido) if corrigido else ""

    # limites absolutos para evitar confusão com pavimentos
    if banheiros is not None and banheiros > 15:
        logger.warning(f"Banheiros suspeito ({banheiros}) — limpando campo")
        dados["banheiros"] = ""

    if quartos is not None and quartos > 30:
        logger.warning(f"Quartos suspeito ({quartos}) — limpando campo")
        dados["quartos"] = ""

    if vagas is not None and vagas > 30:
        logger.warning(f"Vagas suspeito ({vagas}) — limpando campo")
        dados["vagas"] = ""

    if area is not None and area > 100000:
        logger.warning(f"Área suspeita ({area}m²) — limpando campo")
        dados["area_m2"] = ""

    return dados


def _dados_invalidos(dados: dict) -> str:
    """Retorna um motivo quando a resposta da IA/browser nao deve virar rascunho."""
    titulo = str(dados.get("titulo", "")).strip()
    descricao = str(dados.get("descricao_util", "")).strip()
    texto = f"{titulo}\n{descricao}".lower()
    sinais_erro = [
        "erro:",
        "link não acessível",
        "link nao acessivel",
        "não foi possível acessar",
        "nao foi possivel acessar",
        "não consegui acessar",
        "nao consegui acessar",
        "post privado",
        "publicação indisponível",
        "publicacao indisponivel",
    ]
    if not titulo:
        return "Titulo ausente"
    if any(s in texto for s in sinais_erro):
        return "IA/browser indicou que o post nao esta acessivel"
    if len(titulo) < 8 and len(descricao) < 30:
        return "Dados extraidos insuficientes para criar rascunho"
    return ""


# ---------------------------------------------------------------------------
# Extração de dados
# ---------------------------------------------------------------------------

_MOTIVO_MSGS = {
    "url_invalida":           "URL não reconhecida (use Instagram, OLX ou Órulo)",
    "nao_encontrado":         "Anúncio não encontrado (pode ter sido apagado)",
    "acesso_restrito":        "Acesso bloqueado (post privado ou proteção do site)",
    "post_privado":           "Post privado ou sem descrição pública",
    "erro_rede":              "Falha de rede ao acessar o link",
    "estrutura_desconhecida": "Não foi possível ler os dados da página",
    "cadastro_orulo_incompleto": "Órulo pediu atualização cadastral antes de liberar o imóvel",
    "dados_insuficientes":    "Dados insuficientes para cadastrar o imóvel",
}

# Motivos que não adianta tentar via browser — erro definitivo de URL/acesso
_MOTIVOS_DEFINITIVOS = {"url_invalida", "nao_encontrado"}


async def _via_api(url: str, config: dict, apify_item_task=None) -> tuple[dict | None, str]:
    """
    Extrai legenda via Chrome (já logado no Instagram) e processa com DeepSeek API.
    Retorna (dados, motivo_falha). motivo_falha é '' em caso de sucesso.
    """
    post = {"ok": False, "motivo": "erro_rede"}

    # Quando a Apify ja foi acionada para baixar midia, reaproveita o mesmo
    # resultado para a legenda e evita tentar Chrome/HTTP antes dela.
    if apify_item_task is not None:
        from modules.instagram_media_api import ApifyMediaExtractor
        apify = ApifyMediaExtractor(
            token=config["apify_api_token"],
            downloads_path=config["downloads_path"],
            actor_id=config.get("apify_actor_id", ""),
        )
        try:
            item = await apify_item_task
            post = apify.post_from_item(item, url)
            if post.get("ok"):
                logger.info("Apify compartilhado: legenda obtida sem chamada extra")
        except Exception as e:
            logger.warning(f"Apify compartilhado para caption falhou: {e}")

    # Tenta extrair via Chrome primeiro apenas onde esse caminho existe.
    if not post.get("ok") and sys.platform == "win32":
        post = await extrair_via_chrome(url)

    # Fallback: scraper HTTP (funciona para posts públicos sem login)
    if not post.get("ok"):
        motivo = post.get("motivo", "erro_rede")
        if motivo not in ("url_invalida",):
            origem_falha = "Chrome scraper" if sys.platform == "win32" else "Extração inicial"
            logger.info(f"{origem_falha} falhou ({motivo}), tentando HTTP scraper...")
            scraper = InstagramScraper()
            post = await scraper.extrair(url)

    # Fallback: Apify (extrai caption + mídia, não bloqueia VPS)
    if not post.get("ok") and config.get("usar_apify") and config.get("apify_api_token", "").strip():
        motivo = post.get("motivo", "erro_rede")
        if motivo not in ("url_invalida", "nao_encontrado"):
            logger.info(f"HTTP scraper falhou ({motivo}), tentando Apify para caption...")
            from modules.instagram_media_api import ApifyMediaExtractor
            apify = ApifyMediaExtractor(
                token=config["apify_api_token"],
                downloads_path=config["downloads_path"],
                actor_id=config.get("apify_actor_id", ""),
            )
            if apify_item_task is not None:
                try:
                    item = await apify_item_task
                    post = apify.post_from_item(item, url)
                except Exception as e:
                    logger.warning(f"Apify compartilhado para caption falhou: {e}")
                    post = {"ok": False, "motivo": "erro_rede"}
            else:
                post = await apify.extrair_post(url)

    if not post.get("ok"):
        return None, post.get("motivo", "erro_rede")

    cliente = DeepSeekAPIClient(config["deepseek_api_key"])
    dados = await cliente.extrair(
        caption=post["caption"],
        url_publicacao=post["url_publicacao"],
        perfil_instagram=post.get("perfil_instagram", ""),
    )
    return dados, ""


_SINAIS_INACESSIVEL = [
    "não foi possível acessar",
    "nao foi possivel acessar",
    "não consigo acessar",
    "nao consigo acessar",
    "verifique se o link está correto",
    "verifique se a publicação é pública",
    "publicação não está disponível",
    "conteúdo não disponível",
    "unable to access",
    "cannot access",
]

async def _via_browser(url: str, config: dict) -> tuple[dict | None, str]:
    """Extrai dados via DeepSeek browser. Retorna (dados, motivo_erro)."""
    deepseek = DeepSeekClient(config.get("deepseek_profile_path", "C:\\chrome_bot_deepseek"))
    parser = DeepSeekParser()
    resposta = await deepseek.extrair(url)
    if not resposta:
        return None, "erro_rede"
    # Detecta resposta indicando post privado ou inacessível
    resp_lower = resposta.lower()
    if any(s in resp_lower for s in _SINAIS_INACESSIVEL) and len(resposta) < 600:
        logger.warning(f"DeepSeek indicou post inacessível: {resposta[:200]}")
        return None, "acesso_restrito"
    dados = parser.parse(resposta)
    return (dados, "") if dados and dados.get("titulo") else (None, "erro_rede")


# ---------------------------------------------------------------------------
# Extração + download em paralelo (usado pelo pipeline)
# ---------------------------------------------------------------------------

async def _extrair_e_baixar(url: str, config: dict) -> tuple:
    """
    Roda extração Deepseek E download de mídia simultaneamente.
    Retorna (dados, tipo_midia, arquivo_midia, motivo_falha).
    """
    usar_api = bool(config.get("usar_deepseek_api")) and bool(config.get("deepseek_api_key", "").strip())
    media = MediaResolver(config["downloads_path"], config)

    if usar_api:
        apify_item_task = None
        if config.get("usar_apify") and config.get("apify_api_token", "").strip():
            from modules.instagram_media_api import ApifyMediaExtractor
            apify = ApifyMediaExtractor(
                token=config["apify_api_token"],
                downloads_path=config["downloads_path"],
                actor_id=config.get("apify_actor_id", ""),
            )
            apify_item_task = asyncio.create_task(apify.obter_item(url))

        # Dispara download de mídia ao mesmo tempo que a extração API
        med_task = asyncio.create_task(media.resolver(url, apify_item_task=apify_item_task))
        dados, motivo = await _via_api(url, config, apify_item_task=apify_item_task)

        if not dados and motivo in _MOTIVOS_DEFINITIVOS:
            # URL inválida/não encontrada — cancela download
            med_task.cancel()
            if apify_item_task and not apify_item_task.done():
                apify_item_task.cancel()
            try:
                await med_task
            except asyncio.CancelledError:
                pass
            return None, None, None, motivo

        if not dados and sys.platform == "win32":
            # Fallback browser — só funciona no Windows com Chrome configurado
            dados, motivo = await _via_browser(url, config)

        timeout_midia = int(config.get("midia_download_timeout_seg", MEDIA_DOWNLOAD_TIMEOUT) or MEDIA_DOWNLOAD_TIMEOUT)
        try:
            tipo_midia, arquivo_midia = await asyncio.wait_for(med_task, timeout=timeout_midia)
        except asyncio.TimeoutError:
            logger.warning(f"Download de mídia excedeu {timeout_midia}s — cancelando e continuando sem mídia")
            med_task.cancel()
            try:
                await med_task
            except asyncio.CancelledError:
                pass
            tipo_midia, arquivo_midia = None, []
        except asyncio.CancelledError:
            tipo_midia, arquivo_midia = None, []

        if not dados:
            for f in (arquivo_midia or []):
                try:
                    os.remove(f)
                except Exception:
                    pass
            return None, None, None, motivo

        return dados, tipo_midia, arquivo_midia, ""

    else:
        # Modo browser — sequencial para não conflitar Chrome com Mercadoi
        dados, motivo = await _via_browser(url, config)
        if not dados:
            return None, None, None, motivo
        tipo_midia, arquivo_midia = await media.resolver(url)
        return dados, tipo_midia, arquivo_midia, ""


# ---------------------------------------------------------------------------
# Processamento de um link
# ---------------------------------------------------------------------------

async def _extrair_olx(url: str, config: dict) -> tuple:
    """Extrai dados e baixa imagens de um anúncio OLX."""
    scraper = OlxScraper(
        zenrows_key=config.get("zenrows_api_key", ""),
        scraperapi_key=config.get("scraperapi_key", ""),
        worker_url=config.get("olx_worker_url", ""),
    )
    resultado = await scraper.extrair(url)
    if not resultado.get("ok"):
        return None, None, None, resultado.get("motivo", "erro_rede")

    dados = resultado["dados"]
    imagens_urls = resultado.get("imagens_urls", [])

    downloads_path = config.get("downloads_path", "/data/downloads")
    arquivo_midia = []
    if imagens_urls:
        arquivo_midia = await scraper.baixar_imagens(imagens_urls, downloads_path)

    tipo_midia = "imagem" if arquivo_midia else None
    return dados, tipo_midia, arquivo_midia, ""


async def _extrair_orulo(url: str, config: dict) -> tuple:
    """Extrai dados e baixa imagens de um empreendimento do Órulo."""
    scraper = OruloScraper(
        email=config.get("orulo_email", ""),
        senha=config.get("orulo_senha", ""),
        profile_path=config.get("orulo_profile_path", ""),
        gallery_cache_ttl_horas=int(config.get("orulo_gallery_cache_ttl_horas", 12) or 0),
    )
    resultado = await scraper.extrair(url)
    if not resultado.get("ok"):
        return None, None, None, resultado.get("motivo", "erro_rede")

    dados = resultado["dados"]
    dados_variacoes = resultado.get("dados_variacoes") or []
    if dados_variacoes:
        dados = dados_variacoes[0]
        dados["_publicacoes_orulo"] = dados_variacoes
    imagens_urls = resultado.get("imagens_urls", [])

    arquivo_midia = []
    if imagens_urls:
        arquivo_midia = await scraper.baixar_imagens(
            imagens_urls, config.get("downloads_path", "")
        )

    tipo_midia = "imagem" if arquivo_midia else None
    return dados, tipo_midia, arquivo_midia, ""


async def _midia_instagram(url: str, config: dict) -> tuple:
    media = MediaResolver(config["downloads_path"], config)
    timeout_midia = int(config.get("midia_download_timeout_seg", MEDIA_DOWNLOAD_TIMEOUT) or MEDIA_DOWNLOAD_TIMEOUT)
    try:
        tipo_midia, arquivo_midia = await asyncio.wait_for(media.resolver(url), timeout=timeout_midia)
    except asyncio.TimeoutError:
        logger.warning(f"Download de mídia excedeu {timeout_midia}s — continuando sem mídia")
        tipo_midia, arquivo_midia = None, []
    return tipo_midia, arquivo_midia


async def _publicar_com_retry(
    dados: dict,
    tipo_midia: str,
    arquivo_midia: list,
    config: dict,
    execution_id: str,
) -> dict | None:
    """Publica/salva um imovel usando o destino configurado."""
    if not dados.get("cep") and config.get("preencher_cep_auto", True):
        cep = await buscar_cep(dados)
        if cep:
            dados["cep"] = cep
            logger.info(f"[{execution_id}] CEP preenchido automaticamente: {cep}")

    if SALVAR_TUDO_COMO_RASCUNHO:
        dados["_forcar_rascunho"] = True
    resultado = None

    # O XML-RPC/REST nao clica no formulario real do Mercadoi: ele cria posts e
    # tenta traduzir campos para taxonomias/metas. Quando o usuario WordPress
    # nao tem upload_files ou alguma taxonomia e recusada, imagens e
    # caracteristicas quebram. Por seguranca, o fluxo historico via navegador
    # fica como padrao para todas as fontes.
    usar_browser_mercadoi = _forcar_fluxo_browser_mercadoi(config) or dados.get("_fonte") == "orulo"

    if usar_browser_mercadoi:
        logger.info(f"[{execution_id}] Usando Playwright Mercadoi (fluxo formulario)")
        async with MercadoiDriver(
            config["mercadoi_url"],
            config.get("mercadoi_profile_path", r"C:\chrome_bot_mercadoi"),
            execution_id=execution_id,
            wp_user=config.get("wordpress_xmlrpc_user", "") or config.get("wordpress_wp_user", ""),
            wp_pass=config.get("wordpress_xmlrpc_password", "") or config.get("wordpress_app_password", ""),
        ) as driver:
            for tentativa in range(1, MAX_TENTATIVAS_MERCADOI + 1):
                if tentativa > 1:
                    logger.info(f"[{execution_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_MERCADOI}...")
                resultado = await driver.preencher_e_salvar(dados, tipo_midia, arquivo_midia)
                if resultado["sucesso"]:
                    break
                if tentativa < MAX_TENTATIVAS_MERCADOI:
                    logger.warning(
                        f"[{execution_id}] Falhou: {resultado.get('mensagem', '')}. "
                        f"Aguardando {ESPERA_ENTRE_TENTATIVAS}s..."
                    )
                    await asyncio.sleep(ESPERA_ENTRE_TENTATIVAS)
    elif _usar_wordpress_api(config):
        modo = "Application Password" if config.get("wordpress_wp_user") else "API Key"
        logger.info(f"[{execution_id}] Usando WordPress REST API ({modo})")
        publisher = WordPressPublisher(
            api_url=config["wordpress_api_url"],
            api_key=config.get("wordpress_api_key", ""),
            wp_user=config.get("wordpress_wp_user", ""),
            wp_app_password=config.get("wordpress_app_password", ""),
            execution_id=execution_id,
        )
        for tentativa in range(1, MAX_TENTATIVAS_MERCADOI + 1):
            if tentativa > 1:
                logger.info(f"[{execution_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_MERCADOI}...")
            resultado = await publisher.preencher_e_salvar(dados, tipo_midia, arquivo_midia)
            if resultado["sucesso"]:
                break
            if tentativa < MAX_TENTATIVAS_MERCADOI:
                logger.warning(
                    f"[{execution_id}] Falhou: {resultado.get('mensagem', '')}. "
                    f"Aguardando {ESPERA_ENTRE_TENTATIVAS}s..."
                )
                await asyncio.sleep(ESPERA_ENTRE_TENTATIVAS)
    elif _usar_wordpress_xmlrpc(config):
        logger.info(f"[{execution_id}] Usando WordPress XML-RPC")
        publisher = WordPressXmlRpcPublisher(
            site_url=config["wordpress_xmlrpc_url"],
            wp_user=config["wordpress_xmlrpc_user"],
            wp_password=config["wordpress_xmlrpc_password"],
            execution_id=execution_id,
        )
        for tentativa in range(1, MAX_TENTATIVAS_MERCADOI + 1):
            if tentativa > 1:
                logger.info(f"[{execution_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_MERCADOI}...")
            resultado = await publisher.preencher_e_salvar(dados, tipo_midia, arquivo_midia)
            if resultado["sucesso"]:
                break
            if tentativa < MAX_TENTATIVAS_MERCADOI:
                logger.warning(
                    f"[{execution_id}] Falhou: {resultado.get('mensagem', '')}. "
                    f"Aguardando {ESPERA_ENTRE_TENTATIVAS}s..."
                )
                await asyncio.sleep(ESPERA_ENTRE_TENTATIVAS)
    else:
        logger.info(f"[{execution_id}] Usando Playwright (modo legado)")
        async with MercadoiDriver(
            config["mercadoi_url"],
            config.get("mercadoi_profile_path", r"C:\chrome_bot_mercadoi"),
            execution_id=execution_id,
            wp_user=config.get("wordpress_xmlrpc_user", "") or config.get("wordpress_wp_user", ""),
            wp_pass=config.get("wordpress_xmlrpc_password", "") or config.get("wordpress_app_password", ""),
        ) as driver:
            for tentativa in range(1, MAX_TENTATIVAS_MERCADOI + 1):
                if tentativa > 1:
                    logger.info(f"[{execution_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_MERCADOI}...")
                resultado = await driver.preencher_e_salvar(dados, tipo_midia, arquivo_midia)
                if resultado["sucesso"]:
                    break
                if tentativa < MAX_TENTATIVAS_MERCADOI:
                    logger.warning(
                        f"[{execution_id}] Falhou: {resultado.get('mensagem', '')}. "
                        f"Aguardando {ESPERA_ENTRE_TENTATIVAS}s..."
                    )
                    await asyncio.sleep(ESPERA_ENTRE_TENTATIVAS)

    return resultado


async def processar_link(row: dict, sheet, config: dict):
    url = normalizar_olx_url(row["url_instagram"])
    if orulo_url_valida(url):
        url = normalizar_orulo_url(url)
    row_index = row["_row_index"]
    execution_id = str(uuid.uuid4())[:8].upper()

    logger.info(f"[{execution_id}] === Iniciando: {url} ===")
    _inicio = time.time()
    _t_etapa = _inicio
    sheet.atualizar_status(row_index, "capturando")
    sheet.atualizar_campo(row_index, "id_execucao", execution_id)

    status = StatusWriter(sheet, row_index)

    cache_ttl = int(config.get("cache_extracao_ttl_horas", 12) or 0)

    # --- ETAPA 1+2: Extração e download (rota por fonte) ---
    if orulo_url_valida(url):
        logger.info(f"[{execution_id}] Fonte: Órulo")
        sheet.atualizar_campo(row_index, "origem", "Órulo")
        dados, tipo_midia, arquivo_midia, motivo_falha = await _extrair_orulo(url, config)
        if dados:
            dados["_fonte"] = "orulo"
            if config.get("orulo_agent_id", "").strip():
                dados["_mercadoi_agent_id"] = str(config["orulo_agent_id"]).strip()
    elif olx_url_valida(url):
        logger.info(f"[{execution_id}] Fonte: OLX")
        sheet.atualizar_campo(row_index, "origem", "OLX")
        dados, tipo_midia, arquivo_midia, motivo_falha = await _extrair_olx(url, config)
        if dados:
            dados["_fonte"] = "olx"
    else:
        sheet.atualizar_campo(row_index, "origem", "Instagram")
        cache = sheet.obter_cache(url, cache_ttl) if cache_ttl > 0 else None
        if cache and cache.get("dados"):
            logger.info(f"[{execution_id}] Cache de extracao usado para Instagram ({cache.get('atualizado_em', '')})")
            dados = cache["dados"]
            dados["_fonte"] = "instagram"
            motivo_falha = ""
            tipo_midia, arquivo_midia = await _midia_instagram(url, config)
        else:
            dados, tipo_midia, arquivo_midia, motivo_falha = await _extrair_e_baixar(url, config)
            if dados:
                cache_dados = dict(dados)
                cache_dados.pop("_fonte", None)
                sheet.salvar_cache(url, "Instagram", cache_dados)

    captura_seg = int(time.time() - _t_etapa)
    sheet.atualizar_campo(row_index, "captura_seg", str(captura_seg))

    if not dados or not dados.get("titulo"):
        msg_final = _MOTIVO_MSGS.get(motivo_falha, "Não foi possível extrair dados do imóvel")
        logger.error(f"[{execution_id}] Extração falhou: {msg_final}")
        status.erro("erro_extracao", msg_final)
        return

    # Validação e sanitização dos dados
    dados = _validar_dados(dados)
    dados = aplicar_tipos_imovel(dados)
    motivo_invalido = _dados_invalidos(dados)
    if motivo_invalido:
        logger.error(f"[{execution_id}] Dados rejeitados: {motivo_invalido}")
        status.erro("erro_extracao", motivo_invalido)
        return

    if not dados.get("url_publicacao"):
        dados["url_publicacao"] = url

    whatsapp_olx = _normalizar_whatsapp_url(row.get("whatsapp_contato", ""))
    if olx_url_valida(url) and whatsapp_olx:
        dados["whatsapp_url"] = whatsapp_olx
        logger.info(f"[{execution_id}] WhatsApp OLX anexado ao anuncio")

    # --- Melhoria: whatsapp_default como fallback de contato ---
    if not dados.get("whatsapp_url") and config.get("whatsapp_default", "").strip():
        dados["whatsapp_url"] = config["whatsapp_default"].strip()
        logger.info(f"[{execution_id}] WhatsApp: usando padrão do config")

    if SALVAR_TUDO_COMO_RASCUNHO:
        dados["_forcar_rascunho"] = True
        logger.info(f"[{execution_id}] Modo rascunho ativo: publicacao direta desativada")

    sheet.atualizar_campo(row_index, "titulo_gerado", dados.get("titulo", ""))
    logger.info(f"[{execution_id}] Título: {dados.get('titulo', '')[:70]}")

    for campo in ["tipo_imovel", "operacao", "preco", "quartos", "suites",
                  "banheiros", "cidade_extraida", "bairro_extraido"]:
        val = dados.get(campo, "")
        if val:
            logger.info(f"[{execution_id}]   {campo}: {val}")

    _t_etapa = time.time()
    sheet.atualizar_status(row_index, "baixando_midia")
    sheet.atualizar_campo(row_index, "tipo_midia", tipo_midia or "")
    sheet.atualizar_campo(
        row_index, "arquivo_midia",
        f"{len(arquivo_midia)} arquivo(s)" if arquivo_midia else ""
    )

    # --- OCR de preço: fallback se vazio, ou validador se parece incompleto ---
    preco_atual = dados.get("preco", "")
    preco_curto = preco_atual and len(preco_atual) < 4  # ex: "350" quando deveria ser "350000"
    if (not preco_atual or preco_curto) and arquivo_midia:
        preco_ocr = extrair_preco_de_imagens(arquivo_midia)
        if preco_ocr and (not preco_atual or len(preco_ocr) > len(preco_atual)):
            dados["preco"] = preco_ocr
            dados = _validar_dados(dados)
            acao = "corrigido" if preco_curto else "obtido"
            if dados.get("preco"):
                logger.info(f"[{execution_id}] Preço {acao} via OCR: {dados['preco']}")
            else:
                logger.warning(f"[{execution_id}] Preço OCR descartado por validação: {preco_ocr}")

    midia_seg = int(time.time() - _t_etapa)
    sheet.atualizar_campo(row_index, "midia_seg", str(midia_seg))

    # --- ETAPA 3: Publicação no Mercadoi com retry ---
    if not arquivo_midia:
        # OLX / Órulo: continua sem imagens (dados já extraídos da página pública)
        if dados.get("_fonte") in ("olx", "orulo"):
            logger.warning(f"[{execution_id}] {dados['_fonte'].upper()} sem imagens — continuando sem mídia")
        else:
            msg = "Nenhuma midia foi baixada para esta publicacao"
            logger.error(f"[{execution_id}] {msg}")
            status.erro("erro_download", msg)
            return

    _t_etapa = time.time()
    sheet.atualizar_status(row_index, "enviando_wp")
    logger.info(f"[{execution_id}] Publicando no Mercadoi...")
    resultado = await _publicar_com_retry(dados, tipo_midia, arquivo_midia, config, execution_id)

    if resultado and resultado["sucesso"]:
        publicacao_seg = int(time.time() - _t_etapa)
        sheet.atualizar_campo(row_index, "publicacao_seg", str(publicacao_seg))
        sheet.atualizar_campo(row_index, "cidade_aplicada", resultado.get("cidade_aplicada", ""))
        sheet.atualizar_campo(row_index, "bairro_aplicado", resultado.get("bairro_aplicado", ""))
        mercadoi_url = resultado.get("mercadoi_url", "")
        sheet.atualizar_campo(row_index, "mercadoi_url", mercadoi_url)
        sheet.atualizar_campo(row_index, "url_publica", resultado.get("url_publica", ""))
        # Define status: publicado diretamente ou rascunho
        msg = resultado.get("mensagem", "")
        status_final = "publicado" if "Publicado" in msg else "rascunho_salvo"
        tempo_total = int(time.time() - _inicio)
        sheet.atualizar_campo(row_index, "tempo_seg", str(tempo_total))
        falha_extra_msg = ""
        extras_orulo = dados.get("_publicacoes_orulo") or []
        media_ids_reuso = resultado.get("media_ids") or []
        if len(extras_orulo) > 1:
            logger.info(f"[{execution_id}] Orulo: publicando mais {len(extras_orulo) - 1} tipologia(s)")
            urls_extra = []
            falhas_extra = []
            publicacoes_extra_ok = 0
            for idx, dados_extra in enumerate(extras_orulo[1:], 2):
                dados_extra = dict(dados_extra)
                dados_extra["_fonte"] = "orulo"
                if config.get("orulo_agent_id", "").strip():
                    dados_extra["_mercadoi_agent_id"] = str(config["orulo_agent_id"]).strip()
                if not dados_extra.get("url_publicacao"):
                    dados_extra["url_publicacao"] = url
                if not dados_extra.get("whatsapp_url") and config.get("whatsapp_default", "").strip():
                    dados_extra["whatsapp_url"] = config["whatsapp_default"].strip()
                if SALVAR_TUDO_COMO_RASCUNHO:
                    dados_extra["_forcar_rascunho"] = True
                if media_ids_reuso and _usar_wordpress_xmlrpc(config):
                    dados_extra["_wp_media_ids"] = media_ids_reuso
                dados_extra = _validar_dados(dados_extra)
                dados_extra = aplicar_tipos_imovel(dados_extra)
                motivo_extra = _dados_invalidos(dados_extra)
                if motivo_extra:
                    falhas_extra.append(f"{idx}/{len(extras_orulo)}: {motivo_extra}")
                    continue
                logger.info(
                    f"[{execution_id}] Orulo tipologia {idx}/{len(extras_orulo)}: "
                    f"{dados_extra.get('titulo', '')[:70]}"
                )
                _t_extra = time.time()
                resultado_extra = await _publicar_com_retry(
                    dados_extra, tipo_midia, arquivo_midia, config, execution_id
                )
                if resultado_extra and resultado_extra.get("sucesso"):
                    publicacoes_extra_ok += 1
                    msg_extra = resultado_extra.get("mensagem", "")
                    status_extra = "publicado" if "Publicado" in msg_extra else "rascunho_salvo"
                    try:
                        sheet.registrar_publicacao_extra(
                            base_row_index=row_index,
                            dados=dados_extra,
                            resultado=resultado_extra,
                            status=status_extra,
                            execution_id=f"{execution_id}-{idx}",
                            origem="Órulo",
                            tipo_midia=tipo_midia or "",
                            arquivo_midia=f"{len(arquivo_midia)} arquivo(s)" if arquivo_midia else "",
                            tempo_seg=tempo_total,
                            captura_seg=captura_seg,
                            midia_seg=midia_seg,
                            publicacao_seg=int(time.time() - _t_extra),
                        )
                    except Exception as e:
                        logger.warning(f"[{execution_id}] Falha ao registrar historico extra {idx}: {e}")
                    if resultado_extra.get("mercadoi_url"):
                        urls_extra.append(resultado_extra["mercadoi_url"])
                else:
                    erro_extra = resultado_extra.get("mensagem", "") if resultado_extra else "resultado nulo"
                    falhas_extra.append(f"{idx}/{len(extras_orulo)}: {erro_extra}")

            if urls_extra:
                logger.info(f"[{execution_id}] {len(urls_extra)} link(s) extra(s) do Mercadoi registrados no historico")
            if falhas_extra:
                msg = f"{msg}; {1 + publicacoes_extra_ok}/{len(extras_orulo)} publicacoes concluidas; falhas: {'; '.join(falhas_extra)}"
                falha_extra_msg = msg
            else:
                msg = f"{msg}: {len(extras_orulo)} publicacoes"

        if falha_extra_msg:
            status.erro("erro_preenchimento", falha_extra_msg)
            logger.error(f"[{execution_id}] Falha parcial no Orulo: {falha_extra_msg}")
        else:
            status.sucesso(status_final, msg)
            logger.info(
                f"[{execution_id}] Sucesso! ({tempo_total}s) | "
                f"captura={captura_seg}s midia={midia_seg}s publicacao={publicacao_seg}s"
            )
        for arq in arquivo_midia:
            try:
                os.remove(arq)
            except Exception:
                pass
        if arquivo_midia:
            logger.info(f"[{execution_id}] {len(arquivo_midia)} arquivo(s) de mídia removido(s)")
    else:
        publicacao_seg = int(time.time() - _t_etapa)
        sheet.atualizar_campo(row_index, "publicacao_seg", str(publicacao_seg))
        msg = resultado.get("mensagem", "") if resultado else "resultado nulo"
        screenshot = resultado.get("screenshot_path", "") if resultado else ""
        if screenshot:
            msg += f" | screenshot salvo: {screenshot}"
        status.erro(
            resultado.get("status_erro", "erro_preenchimento") if resultado else "erro_preenchimento",
            msg,
        )
        logger.error(f"[{execution_id}] Falha definitiva: {msg}")


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------


async def _verificar_chrome() -> bool:
    """Retorna True se o Chrome com remote debugging está acessível na porta 9222."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://localhost:9222/json/version")
            return r.status_code == 200
    except Exception:
        return False


def _limpar_arquivos_antigos(config: dict, data_dir: str, db: DatabaseManager) -> None:
    dias = int(config.get("limpeza_auto_dias", 15) or 0)
    if dias <= 0:
        return
    agora = time.time()
    limite = agora - (dias * 86400)
    pastas = [
        config.get("downloads_path", ""),
        os.path.join(data_dir, "downloads"),
        os.path.join(data_dir, "logs"),
        os.path.join(data_dir, "logs", "screenshots"),
    ]
    removidos = 0
    vistos = set()
    for pasta in pastas:
        if not pasta or pasta in vistos or not os.path.isdir(pasta):
            continue
        vistos.add(pasta)
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                try:
                    if os.path.getmtime(caminho) < limite:
                        os.remove(caminho)
                        removidos += 1
                except Exception:
                    pass
    try:
        removidos += db.limpar_cache_expirado(max(1, min(dias, 3)))
    except Exception:
        pass
    if removidos:
        logger.info(f"Limpeza automática: {removidos} arquivo/cache antigo removido(s)")


def _categoria_falha(status: str, msg: str) -> str:
    texto = f"{status} {msg}".lower()
    if "extracao" in texto or "extra" in texto or "ler os dados" in texto:
        return "extração"
    if "download" in texto or "midia" in texto or "mídia" in texto:
        return "mídia"
    if "timeout" in texto:
        return "timeout"
    if "wordpress" in texto or "xml-rpc" in texto or "api" in texto:
        return "WordPress"
    if "login" in texto or "autentic" in texto or "sess" in texto:
        return "login"
    return "publicação"


async def executar_ciclo(config: dict):
    """Lê pendentes e processa. Retorna o número de itens processados."""
    base_dir  = os.path.dirname(os.path.abspath(__file__))
    data_dir  = os.environ.get("BOT_DATA_DIR", base_dir)

    # Correções automáticas para VPS (Linux) — sem alterar o config.json em disco
    if sys.platform != "win32":
        config = dict(config)
        # Força modo DeepSeek API se a chave estiver configurada mas browser mode estiver ativo
        if not config.get("usar_deepseek_api") and config.get("deepseek_api_key", "").strip():
            config["usar_deepseek_api"] = True
            logger.info("VPS: DeepSeek API ativado automaticamente (Chrome nao disponivel no Linux)")
        # Corrige downloads_path com caminho Windows (C:\...) ou vazio
        dp = config.get("downloads_path", "")
        if not dp or not dp.startswith("/"):
            config["downloads_path"] = os.path.join(data_dir, "downloads")
            os.makedirs(config["downloads_path"], exist_ok=True)
            logger.info(f"VPS: downloads_path corrigido para {config['downloads_path']}")

    db_path   = config.get("db_path", os.path.join(data_dir, "botmercadoi.db"))
    db = DatabaseManager(db_path)
    _limpar_arquivos_antigos(config, data_dir, db)

    db.resetar_timeout(minutos=10)
    db.resetar_travados()
    pendentes = db.listar_pendentes()

    if not pendentes:
        return 0

    tem_orulo_pendente = any(orulo_url_valida(normalizar_orulo_url(row["url_instagram"])) for row in pendentes)
    tem_browser_pendente = _forcar_fluxo_browser_mercadoi(config) or tem_orulo_pendente

    if (
        (tem_browser_pendente or (not _usar_wordpress_api(config) and not _usar_wordpress_xmlrpc(config)))
        and sys.platform == "win32"
        and not await _verificar_chrome()
    ):
        logger.error("Chrome do Mercadoi não está aberto. Abra o Chrome com remote debugging na porta 9222.")
        await notificar(config, "⚠️ <b>Bot Mercadoi</b> — Chrome não está aberto.\nAbra o Chrome com remote debugging para retomar o processamento.")
        return 0

    urls_processadas = {row["url_instagram"].strip() for row in pendentes}

    max_workers = int(config.get("max_workers", 1))

    # Paralelismo seguro apenas quando não há browser no caminho crítico.
    # Browser DeepSeek ou Playwright para publicação não suportam múltiplas
    # instâncias simultâneas no mesmo Chrome.
    sem_browser_publicacao = (_usar_wordpress_api(config) or _usar_wordpress_xmlrpc(config)) and not tem_browser_pendente
    modo_api_completo = sem_browser_publicacao and bool(config.get("usar_deepseek_api"))
    if max_workers > 1 and not modo_api_completo:
        logger.warning(
            f"max_workers={max_workers} ignorado — paralelismo requer "
            f"(usar_wordpress_api ou wordpress_xmlrpc) e usar_deepseek_api=true. Rodando com 1 worker."
        )
        max_workers = 1

    if max_workers > 1:
        logger.info(f"Processando {len(pendentes)} link(s) com {max_workers} workers em paralelo")
    else:
        logger.info(f"Processando {len(pendentes)} link(s) sequencialmente")

    semaforo = asyncio.Semaphore(max_workers)

    async def _processar_com_semaforo(row: dict):
        async with semaforo:
            try:
                await processar_link(row, db, config)
            except Exception as e:
                logger.error(f"Erro inesperado: {e}")
                try:
                    db.atualizar_status(row["_row_index"], "erro_preenchimento")
                    db.atualizar_campo(row["_row_index"], "mensagem_erro", str(e))
                except Exception:
                    pass

    await asyncio.gather(*[_processar_com_semaforo(row) for row in pendentes])

    total = len(pendentes)

    # Contabiliza apenas os itens deste ciclo para a notificação
    _, rows_after = db._todas_as_linhas()
    processadas = [r for r in rows_after if r.get("url_instagram", "").strip() in urls_processadas]
    _STATUS_SUCESSO = ("rascunho_salvo", "rascunho_salvo_sem_midia_video", "publicado")
    sucessos     = sum(1 for r in processadas if r.get("status", "") in _STATUS_SUCESSO)
    falhas_list  = [r for r in processadas if "erro" in r.get("status", "").lower()]

    if total:
        sucessos_list = [r for r in processadas if r.get("status", "") in _STATUS_SUCESSO]
        linhas = []
        for r in sucessos_list[:10]:
            titulo = (r.get("titulo_gerado") or "")[:50] or r.get("url_instagram", "")[:50]
            url_m = r.get("mercadoi_url", "")
            linhas.append(f"  ✅ <a href='{url_m}'>{titulo}</a>" if url_m else f"  ✅ {titulo}")
        for r in falhas_list[:5]:
            cat = _categoria_falha(r.get("status", ""), r.get("mensagem_erro", ""))
            motivo = (r.get("mensagem_erro") or r.get("status", "") or "falha")[:70]
            linhas.append(f"  ❌ [{cat}] {r.get('url_instagram','')[:45]} — {motivo}")
        mais_suc = f"\n  (+ {len(sucessos_list)-10} outros)" if len(sucessos_list) > 10 else ""
        mais_err = f"\n  (+ {len(falhas_list)-5} outros erros)" if len(falhas_list) > 5 else ""
        corpo = "\n".join(linhas) + mais_suc + mais_err
        tempos = [int(r.get("tempo_seg") or 0) for r in sucessos_list if int(r.get("tempo_seg") or 0) > 0]
        tempo_txt = f"\n⏱️ Média: {sum(tempos)//len(tempos)}s/pub" if tempos else ""
        if falhas_list:
            msg = (
                f"⚠️ <b>Bot Mercadoi</b> — Ciclo concluído\n"
                f"✅ {sucessos} publicado(s)  |  ❌ {len(falhas_list)} falha(s){tempo_txt}\n\n"
                f"{corpo}"
            )
        else:
            msg = (
                f"✅ <b>Bot Mercadoi</b> — {total} imóvel(is) publicado(s)!{tempo_txt}\n\n"
                f"{corpo}"
            )
        await notificar(config, msg)

    return total


async def main(watch: bool = False, intervalo: int = 5):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    if not os.path.exists(config_path):
        logger.error(f"config.json não encontrado em {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # config.json pode sobrescrever o intervalo
    intervalo = config.get("watch_intervalo_minutos", intervalo)

    if watch:
        logger.info(f"=== Bot Mercadoi v{VERSION} — Modo Watch ({intervalo} min) ===")
        await notificar(config, f"🤖 <b>Bot Mercadoi iniciado</b> — monitorando a cada {intervalo} min.")
        ciclo = 1
        while True:
            logger.info(f"--- Ciclo #{ciclo} ---")
            # Recarrega config a cada ciclo para capturar mudanças feitas no painel
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                intervalo = config.get("watch_intervalo_minutos", intervalo)
            except Exception as e:
                logger.warning(f"Não foi possível recarregar config.json: {e}")
            await executar_ciclo(config)
            logger.info(f"Aguardando {intervalo} minuto(s)...")
            await asyncio.sleep(intervalo * 60)
            ciclo += 1
    else:
        logger.info(f"=== Bot Mercadoi v{VERSION} ===")
        await executar_ciclo(config)
        logger.info("=== Processamento concluído ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot Mercadoi")
    parser.add_argument(
        "--watch",
        nargs="?",
        const=5,
        type=int,
        metavar="MINUTOS",
        help="Rodar em loop contínuo a cada N minutos (padrão: 5)",
    )
    args = parser.parse_args()

    asyncio.run(main(watch=args.watch is not None, intervalo=args.watch or 5))
