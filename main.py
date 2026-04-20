"""
Automacao de cadastro de anuncios imobiliarios no Mercadoi
Versao: 3.1
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
from modules.deepseek_api import DeepSeekAPIClient
from modules.deepseek_client import DeepSeekClient
from modules.deepseek_parser import DeepSeekParser
from modules.media_resolver import MediaResolver
from modules.mercadoi_driver import MercadoiDriver
from modules.status_writer import StatusWriter
from modules.logger import Logger
from modules.notificador import notificar

logger = Logger("main")

MAX_TENTATIVAS_MERCADOI = 3
ESPERA_ENTRE_TENTATIVAS = 10  # segundos


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
    Tenta extrair dados via Instagram scraper + DeepSeek API oficial.
    Retorna (dados, motivo_falha). motivo_falha é '' em caso de sucesso.
    """
    scraper = InstagramScraper()
    post = await scraper.extrair(url)
    if not post.get("ok"):
        return None, post.get("motivo", "erro_rede")
    cliente = DeepSeekAPIClient(config["deepseek_api_key"])
    dados = await cliente.extrair(
        caption=post["caption"],
        url_publicacao=post["url_publicacao"],
        perfil_instagram=post.get("perfil_instagram", ""),
    )
    return dados, ""


async def _via_browser(url: str, config: dict) -> dict | None:
    """Extrai dados via DeepSeek browser (fallback)."""
    deepseek = DeepSeekClient(config.get("deepseek_profile_path", "C:\\chrome_bot_deepseek"))
    parser = DeepSeekParser()
    resposta = await deepseek.extrair(url)
    if not resposta:
        return None
    dados = parser.parse(resposta)
    return dados if dados and dados.get("titulo") else None


# ---------------------------------------------------------------------------
# Processamento de um link
# ---------------------------------------------------------------------------

async def processar_link(row: dict, sheet, config: dict):
    url = row["url_instagram"]
    row_index = row["_row_index"]
    execution_id = str(uuid.uuid4())[:8].upper()

    logger.info(f"[{execution_id}] === Iniciando: {url} ===")
    sheet.atualizar_status(row_index, "processando")
    sheet.atualizar_campo(row_index, "id_execucao", execution_id)

    status = StatusWriter(sheet, row_index)

    # --- ETAPA 1: Extração de dados ---
    dados = None
    motivo_falha = ""
    tem_api_key = bool(config.get("deepseek_api_key", "").strip())

    if tem_api_key:
        logger.info(f"[{execution_id}] Extraindo via API DeepSeek...")
        dados, motivo_falha = await _via_api(url, config)
        if dados:
            logger.info(f"[{execution_id}] Dados obtidos via API")
        else:
            msg_motivo = _MOTIVO_MSGS.get(motivo_falha, motivo_falha)
            if motivo_falha in _MOTIVOS_DEFINITIVOS:
                logger.error(f"[{execution_id}] Extração falhou definitivamente: {msg_motivo}")
                status.erro("erro_extracao", msg_motivo)
                return
            logger.warning(f"[{execution_id}] API falhou ({msg_motivo}) — tentando fallback browser")

    if not dados:
        logger.info(f"[{execution_id}] Extraindo via DeepSeek browser...")
        dados = await _via_browser(url, config)

    if not dados or not dados.get("titulo"):
        msg_final = _MOTIVO_MSGS.get(motivo_falha, "Não foi possível extrair dados do imóvel")
        logger.error(f"[{execution_id}] Extração falhou em todos os métodos: {msg_final}")
        status.erro("erro_extracao", msg_final)
        return

    # Validação e sanitização dos dados
    dados = _validar_dados(dados)

    sheet.atualizar_campo(row_index, "titulo_gerado", dados.get("titulo", ""))
    logger.info(f"[{execution_id}] Título: {dados.get('titulo', '')[:70]}")

    for campo in ["tipo_imovel", "operacao", "preco", "quartos", "suites", "banheiros", "cidade_extraida", "bairro_extraido"]:
        val = dados.get(campo, "")
        if val:
            logger.info(f"[{execution_id}]   {campo}: {val}")

    # --- ETAPA 2: Mídia ---
    logger.info(f"[{execution_id}] Resolvendo mídia...")
    media = MediaResolver(config["downloads_path"])
    tipo_midia, arquivo_midia = await media.resolver(url)
    sheet.atualizar_campo(row_index, "tipo_midia", tipo_midia)
    sheet.atualizar_campo(
        row_index, "arquivo_midia",
        f"{len(arquivo_midia)} arquivo(s)" if arquivo_midia else ""
    )

    # --- ETAPA 3: Publicação no Mercadoi com retry ---
    logger.info(f"[{execution_id}] Publicando no Mercadoi...")
    resultado = None

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
        status.sucesso("rascunho_salvo", resultado.get("mensagem", "Rascunho salvo com sucesso"))
        logger.info(f"[{execution_id}] Sucesso!")
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
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = config.get("db_path", os.path.join(base_dir, "botmercadoi.db"))
    db = DatabaseManager(db_path)

    db.resetar_travados()
    pendentes = db.listar_pendentes()

    if not pendentes:
        return 0

    if not await _verificar_chrome():
        logger.error("Chrome do Mercadoi não está aberto. Abra o Chrome com remote debugging na porta 9222.")
        return 0

    urls_processadas = {row["url_instagram"].strip() for row in pendentes}

    for row in pendentes:
        try:
            await processar_link(row, db, config)
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
            try:
                db.atualizar_status(row["_row_index"], "erro_preenchimento")
                db.atualizar_campo(row["_row_index"], "mensagem_erro", str(e))
            except Exception:
                pass

    total = len(pendentes)

    # Contabiliza apenas os itens deste ciclo para a notificação
    _, rows_after = db._todas_as_linhas()
    processadas = [r for r in rows_after if r.get("url_instagram", "").strip() in urls_processadas]
    sucessos     = sum(1 for r in processadas if r.get("status", "") in ("rascunho_salvo", "rascunho_salvo_sem_midia_video"))
    falhas_list  = [r for r in processadas if "erro" in r.get("status", "").lower()]

    if total:
        if falhas_list:
            erros_txt = "\n".join(f"  • {r.get('url_instagram','')[:60]}" for r in falhas_list[:5])
            mais = f"\n  (+ {len(falhas_list)-5} outros)" if len(falhas_list) > 5 else ""
            msg = (
                f"⚠️ <b>Bot Mercadoi</b> — Ciclo concluído\n"
                f"✅ {sucessos} publicado(s)  |  ❌ {len(falhas_list)} falha(s)\n\n"
                f"<b>Erros:</b>\n{erros_txt}{mais}"
            )
        else:
            msg = f"✅ <b>Bot Mercadoi</b> — {total} imóvel(is) publicado(s) com sucesso!"
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
        ciclo = 1
        while True:
            logger.info(f"--- Ciclo #{ciclo} ---")
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
