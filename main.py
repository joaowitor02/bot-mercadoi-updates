"""
Automacao de cadastro de anuncios imobiliarios no Mercadoi
Versao: 4.0
"""

import argparse
import asyncio
import sys
import json
import os
import uuid
import httpx
from modules.database_manager import DatabaseManager
from modules.instagram_scraper import InstagramScraper
from modules.instagram_chrome_scraper import extrair_via_chrome
from modules.deepseek_api import DeepSeekAPIClient
from modules.deepseek_client import DeepSeekClient
from modules.deepseek_parser import DeepSeekParser
from modules.media_resolver import MediaResolver
from modules.mercadoi_driver import MercadoiDriver
from modules.wordpress_publisher import WordPressPublisher
from modules.wordpress_xmlrpc_publisher import WordPressXmlRpcPublisher
from modules.wordpress_xmlrpc_publisher import WordPressXmlRpcPublisher
from modules.status_writer import StatusWriter
from modules.logger import Logger
from modules.notificador import notificar
from modules.ocr_preco import extrair_preco_de_imagens

logger = Logger("main")

MAX_TENTATIVAS_MERCADOI = 3
ESPERA_ENTRE_TENTATIVAS = 10  # segundos


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


# ---------------------------------------------------------------------------
# Validação dos dados extraídos pela IA
# ---------------------------------------------------------------------------

def _validar_dados(dados: dict) -> dict:
    """
    Sanitiza campos numéricos para corrigir valores absurdos da IA.
    Regras baseadas em limites do mundo real para imóveis residenciais.
    """
    def to_int(v):
        try:
            return int(str(v).strip())
        except Exception:
            return None

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
    "url_invalida":    "URL não é um post válido do Instagram",
    "nao_encontrado":  "Post não encontrado (link pode ter sido apagado)",
    "acesso_restrito": "Instagram bloqueou o acesso (post privado ou conta restrita)",
    "post_privado":    "Post privado ou sem descrição pública",
    "erro_rede":       "Falha de rede ao acessar o Instagram",
}

# Motivos que não adianta tentar via browser — erro definitivo de URL/acesso
_MOTIVOS_DEFINITIVOS = {"url_invalida", "nao_encontrado"}


async def _via_api(url: str, config: dict) -> tuple[dict | None, str]:
    """
    Extrai legenda via Chrome (já logado no Instagram) e processa com DeepSeek API.
    Retorna (dados, motivo_falha). motivo_falha é '' em caso de sucesso.
    """
    # Tenta extrair via Chrome primeiro (logado no Instagram, sem bloqueio)
    post = await extrair_via_chrome(url)

    # Fallback: scraper HTTP (funciona para posts públicos sem login)
    if not post.get("ok"):
        motivo = post.get("motivo", "erro_rede")
        if motivo not in ("url_invalida",):
            logger.info(f"Chrome scraper falhou ({motivo}), tentando HTTP scraper...")
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
        # Dispara download de mídia ao mesmo tempo que a extração API
        med_task = asyncio.create_task(media.resolver(url))
        dados, motivo = await _via_api(url, config)

        if not dados and motivo in _MOTIVOS_DEFINITIVOS:
            # URL inválida/não encontrada — cancela download
            med_task.cancel()
            try:
                await med_task
            except asyncio.CancelledError:
                pass
            return None, None, None, motivo

        if not dados:
            # API falhou por motivo transitório — tenta browser
            # (mídia continua baixando em paralelo)
            dados, motivo = await _via_browser(url, config)

        try:
            tipo_midia, arquivo_midia = await med_task
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

async def processar_link(row: dict, sheet, config: dict):
    url = row["url_instagram"]
    row_index = row["_row_index"]
    execution_id = str(uuid.uuid4())[:8].upper()

    logger.info(f"[{execution_id}] === Iniciando: {url} ===")
    _inicio = __import__("time").time()
    sheet.atualizar_status(row_index, "processando")
    sheet.atualizar_campo(row_index, "id_execucao", execution_id)

    status = StatusWriter(sheet, row_index)

    # --- ETAPA 1+2: Extração (API) e download de mídia em paralelo ---
    dados, tipo_midia, arquivo_midia, motivo_falha = await _extrair_e_baixar(url, config)

    if not dados or not dados.get("titulo"):
        msg_final = _MOTIVO_MSGS.get(motivo_falha, "Não foi possível extrair dados do imóvel")
        logger.error(f"[{execution_id}] Extração falhou: {msg_final}")
        status.erro("erro_extracao", msg_final)
        return

    # Validação e sanitização dos dados
    dados = _validar_dados(dados)
    motivo_invalido = _dados_invalidos(dados)
    if motivo_invalido:
        logger.error(f"[{execution_id}] Dados rejeitados: {motivo_invalido}")
        status.erro("erro_extracao", motivo_invalido)
        return

    if not dados.get("url_publicacao"):
        dados["url_publicacao"] = url

    sheet.atualizar_campo(row_index, "titulo_gerado", dados.get("titulo", ""))
    logger.info(f"[{execution_id}] Título: {dados.get('titulo', '')[:70]}")

    for campo in ["tipo_imovel", "operacao", "preco", "quartos", "suites",
                  "banheiros", "cidade_extraida", "bairro_extraido"]:
        val = dados.get(campo, "")
        if val:
            logger.info(f"[{execution_id}]   {campo}: {val}")

    sheet.atualizar_campo(row_index, "tipo_midia", tipo_midia or "")
    sheet.atualizar_campo(
        row_index, "arquivo_midia",
        f"{len(arquivo_midia)} arquivo(s)" if arquivo_midia else ""
    )

    # --- OCR de preço nas imagens (se não veio no texto) ---
    if not dados.get("preco") and arquivo_midia:
        preco_ocr = extrair_preco_de_imagens(arquivo_midia)
        if preco_ocr:
            dados["preco"] = preco_ocr
            logger.info(f"[{execution_id}] Preço obtido via OCR: {preco_ocr}")

    # --- ETAPA 3: Publicação no Mercadoi com retry ---
    if not arquivo_midia:
        msg = "Nenhuma midia foi baixada para esta publicacao"
        logger.error(f"[{execution_id}] {msg}")
        status.erro("erro_download", msg)
        return

    logger.info(f"[{execution_id}] Publicando no Mercadoi...")
    resultado = None

    if _usar_wordpress_api(config):
        # Modo REST API: plugin (Bearer) ou snippet functions.php (Application Password)
        modo = "Application Password" if config.get("wordpress_wp_user") else "API Key"
        logger.info(f"[{execution_id}] Usando WordPress REST API ({modo})")
        publisher = WordPressPublisher(
            api_url        = config["wordpress_api_url"],
            api_key        = config.get("wordpress_api_key", ""),
            wp_user        = config.get("wordpress_wp_user", ""),
            wp_app_password= config.get("wordpress_app_password", ""),
            execution_id   = execution_id,
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
        # Modo XML-RPC: não exige plugin — só usuário e senha WordPress
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
        # Modo legado: Playwright + Chrome
        logger.info(f"[{execution_id}] Usando Playwright (modo legado)")
        async with MercadoiDriver(
            config["mercadoi_url"],
            config.get("mercadoi_profile_path", r"C:\chrome_bot_mercadoi"),
            execution_id=execution_id,
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

    if resultado and resultado["sucesso"]:
        sheet.atualizar_campo(row_index, "cidade_aplicada", resultado.get("cidade_aplicada", ""))
        sheet.atualizar_campo(row_index, "bairro_aplicado", resultado.get("bairro_aplicado", ""))
        mercadoi_url = resultado.get("mercadoi_url", "")
        sheet.atualizar_campo(row_index, "mercadoi_url", mercadoi_url)
        # Define status: publicado diretamente ou rascunho
        msg = resultado.get("mensagem", "")
        status_final = "publicado" if "Publicado" in msg else "rascunho_salvo"
        tempo_total = int(__import__("time").time() - _inicio)
        sheet.atualizar_campo(row_index, "tempo_seg", str(tempo_total))
        status.sucesso(status_final, msg)
        logger.info(f"[{execution_id}] Sucesso! ({tempo_total}s)")
        for arq in arquivo_midia:
            try:
                os.remove(arq)
            except Exception:
                pass
        if arquivo_midia:
            logger.info(f"[{execution_id}] {len(arquivo_midia)} arquivo(s) de mídia removido(s)")
    else:
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


async def executar_ciclo(config: dict):
    """Lê pendentes e processa. Retorna o número de itens processados."""
    base_dir  = os.path.dirname(os.path.abspath(__file__))
    data_dir  = os.environ.get("BOT_DATA_DIR", base_dir)
    db_path   = config.get("db_path", os.path.join(data_dir, "botmercadoi.db"))
    db = DatabaseManager(db_path)

    db.resetar_travados()
    pendentes = db.listar_pendentes()

    if not pendentes:
        return 0

    if not _usar_wordpress_api(config) and not _usar_wordpress_xmlrpc(config) and not await _verificar_chrome():
        logger.error("Chrome do Mercadoi não está aberto. Abra o Chrome com remote debugging na porta 9222.")
        await notificar(config, "⚠️ <b>Bot Mercadoi</b> — Chrome não está aberto.\nAbra o Chrome com remote debugging para retomar o processamento.")
        return 0

    urls_processadas = {row["url_instagram"].strip() for row in pendentes}

    max_workers = int(config.get("max_workers", 1))

    # Paralelismo seguro apenas quando não há browser no caminho crítico.
    # Browser DeepSeek ou Playwright para publicação não suportam múltiplas
    # instâncias simultâneas no mesmo Chrome.
    sem_browser_publicacao = _usar_wordpress_api(config) or _usar_wordpress_xmlrpc(config)
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
            linhas.append(f"  ❌ {r.get('url_instagram','')[:50]}")
        mais_suc = f"\n  (+ {len(sucessos_list)-10} outros)" if len(sucessos_list) > 10 else ""
        mais_err = f"\n  (+ {len(falhas_list)-5} outros erros)" if len(falhas_list) > 5 else ""
        corpo = "\n".join(linhas) + mais_suc + mais_err
        if falhas_list:
            msg = (
                f"⚠️ <b>Bot Mercadoi</b> — Ciclo concluído\n"
                f"✅ {sucessos} publicado(s)  |  ❌ {len(falhas_list)} falha(s)\n\n"
                f"{corpo}"
            )
        else:
            msg = (
                f"✅ <b>Bot Mercadoi</b> — {total} imóvel(is) publicado(s)!\n\n"
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
        logger.info(f"=== Bot Mercadoi v3.1 — Modo Watch ({intervalo} min) ===")
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
        logger.info("=== Bot Mercadoi v3.1 ===")
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
