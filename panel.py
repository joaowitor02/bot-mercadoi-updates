"""
Painel web local do Bot Mercadoi.
Acesse http://localhost:8000 após iniciar.
"""

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

# Python 3.12 no Windows: ProactorEventLoop gera ValueError em transports fechados.
# SelectorEventLoop evita isso (seguro pois não usamos asyncio.create_subprocess_exec).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="Bot Mercadoi — Painel")

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_SENHA: str = ""
_SESSION_TOKENS: set[str] = set()

# Rotas que não exigem autenticação
_PUBLIC_PATHS = {"/login", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _SENHA or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get("mercadoi_session", "")
        if token in _SESSION_TOKENS:
            return await call_next(request)

        if request.url.path.startswith("/api/") or request.url.path.startswith("/screenshots/"):
            return JSONResponse({"error": "Não autenticado"}, status_code=401)

        return RedirectResponse("/login", status_code=302)


app.add_middleware(AuthMiddleware)


@app.on_event("startup")
async def _startup():
    global _SENHA
    try:
        cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        _SENHA = cfg.get("panel_senha", "").strip()
    except Exception:
        pass


@app.get("/login", response_class=HTMLResponse)
async def login_page(erro: str = ""):
    html = BASE_DIR / "panel_static" / "login.html"
    content = html.read_text(encoding="utf-8")
    if erro:
        content = content.replace("<!--ERRO-->",
            '<p class="text-sm text-red-500 text-center mt-1">Senha incorreta. Tente novamente.</p>')
    return HTMLResponse(content)


@app.post("/login")
async def fazer_login(senha: str = Form(...)):
    if senha == _SENHA:
        token = secrets.token_hex(32)
        _SESSION_TOKENS.add(token)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
        return resp
    return RedirectResponse("/login?erro=1", status_code=303)


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("mercadoi_session", "")
    _SESSION_TOKENS.discard(token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("mercadoi_session")
    return resp


# ---------------------------------------------------------------------------
# Parser de logs
# ---------------------------------------------------------------------------

_STATUS_PATTERNS = [
    (r"Sucesso!", "sucesso"),
    (r"Falha definitiva:", "falha"),
    (r"Extração falhou", "falha"),
    (r"erro_extracao", "falha"),
]


def _parse_logs_dia(data_str: str) -> list[dict]:
    log_file = LOGS_DIR / f"{data_str}.log"
    if not log_file.exists():
        return []

    execucoes: dict[str, dict] = {}

    with open(log_file, encoding="utf-8", errors="replace") as f:
        linhas = f.readlines()

    for linha in linhas:
        m_id = re.search(r"\[([A-F0-9]{8})\]", linha)
        if not m_id:
            continue
        eid = m_id.group(1)

        if eid not in execucoes:
            execucoes[eid] = {
                "execution_id": eid,
                "inicio": "",
                "fim": "",
                "url": "",
                "titulo": "",
                "status": "processando",
                "erros": [],
                "screenshot": "",
                "cidade": "",
                "bairro": "",
                "tipo": "",
            }

        ex = execucoes[eid]

        m_ts = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", linha)
        if m_ts:
            if not ex["inicio"]:
                ex["inicio"] = m_ts.group(1)
            ex["fim"] = m_ts.group(1)

        if "Iniciando:" in linha:
            m = re.search(r"Iniciando: (https?://\S+)", linha)
            if m:
                ex["url"] = m.group(1)

        if "Título:" in linha and not ex["titulo"]:
            m = re.search(r"Título: (.+)$", linha)
            if m:
                ex["titulo"] = m.group(1).strip()

        if "tipo_imovel:" in linha:
            m = re.search(r"tipo_imovel: (.+)$", linha)
            if m:
                ex["tipo"] = m.group(1).strip()

        if "cidade_extraida:" in linha:
            m = re.search(r"cidade_extraida: (.+)$", linha)
            if m:
                ex["cidade"] = m.group(1).strip()

        if "bairro_extraido:" in linha:
            m = re.search(r"bairro_extraido: (.+)$", linha)
            if m:
                ex["bairro"] = m.group(1).strip()

        for pattern, status in _STATUS_PATTERNS:
            if re.search(pattern, linha):
                ex["status"] = status
                if status == "falha":
                    m = re.search(r"(?:Falha definitiva:|falhou[^:]*:?)\s*(.+)$", linha)
                    if m:
                        msg = m.group(1).strip()
                        if msg and msg not in ex["erros"]:
                            ex["erros"].append(msg)
                break

        if "screenshot salvo:" in linha.lower():
            m = re.search(r"screenshot salvo: (\S+)", linha, re.IGNORECASE)
            if m and not ex["screenshot"]:
                ex["screenshot"] = Path(m.group(1)).name

    result = list(execucoes.values())
    result.sort(key=lambda x: x["inicio"], reverse=True)
    return result


def _parse_logs_ultimos_dias(dias: int = 7) -> list[dict]:
    todas = []
    hoje = date.today()
    for i in range(dias):
        d = (hoje - timedelta(days=i)).strftime("%Y-%m-%d")
        for ex in _parse_logs_dia(d):
            ex["data"] = d
            todas.append(ex)
    return todas


def _execucoes_db(dias: int = 7) -> list[dict]:
    db = _db_manager()
    _, rows = db._todas_as_linhas()
    limite = date.today() - timedelta(days=dias - 1)
    itens = []
    for r in rows:
        criado = (r.get("criado_em") or "")[:10]
        fim = r.get("fim_processamento") or ""
        data_ref = (fim or criado)[:10]
        try:
            if data_ref and date.fromisoformat(data_ref) < limite:
                continue
        except Exception:
            pass

        status_raw = (r.get("status") or "").lower()
        if status_raw in ("rascunho_salvo", "rascunho_salvo_sem_midia_video"):
            status = "sucesso"
        elif status_raw == "processando":
            status = "processando"
        elif "erro" in status_raw:
            status = "falha"
        else:
            status = status_raw or "pendente"

        inicio = (r.get("criado_em") or "")[11:19]
        fim_hora = fim[11:19] if "T" in fim else (fim[11:19] if len(fim) >= 19 else "")
        itens.append({
            "execution_id": r.get("id_execucao") or str(r.get("_row_index", "")),
            "row_id": r.get("_row_index"),
            "inicio": inicio,
            "fim": fim_hora,
            "criado_em": r.get("criado_em", ""),
            "atualizado_em": r.get("atualizado_em", ""),
            "data": data_ref or criado,
            "url": r.get("url_instagram", ""),
            "titulo": r.get("titulo_gerado", ""),
            "status": status,
            "status_raw": r.get("status", ""),
            "erros": [r.get("mensagem_erro", "")] if r.get("mensagem_erro") else [],
            "screenshot": "",
            "cidade": r.get("cidade_aplicada", ""),
            "bairro": r.get("bairro_aplicado", ""),
            "tipo": r.get("tipo_midia", ""),
            "arquivo_midia": r.get("arquivo_midia", ""),
            "resultado": r.get("resultado", ""),
            "mercadoi_url": r.get("mercadoi_url", ""),
        })
    itens.sort(key=lambda x: (x.get("data") or "", x.get("fim") or x.get("inicio") or "", x.get("row_id") or 0), reverse=True)
    return itens


# ---------------------------------------------------------------------------
# Estado do bot
# ---------------------------------------------------------------------------

_bot_rodando  = False
_watch_ativo  = False
_bot_processo = None
_ultimo_log: list[str] = []


async def _rodar_bot(watch: bool = False, intervalo: int = 5):
    global _bot_rodando, _watch_ativo, _bot_processo, _ultimo_log
    _bot_rodando = True
    _watch_ativo = watch
    _ultimo_log  = []
    try:
        args = [sys.executable, "main.py"]
        if watch:
            args += ["--watch", str(intervalo)]
        proc = subprocess.Popen(
            args,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        _bot_processo = proc

        _RUIDO_INTERNO = ("proactor_events", "windows_utils", "ResourceWarning",
                          "unclosed transport", "base_subprocess", "base_events",
                          "Traceback (most recent", "File \"C:\\Users", "^^^^")

        def _ler():
            for linha in proc.stdout:
                txt = linha.rstrip()
                if any(s in txt for s in _RUIDO_INTERNO):
                    continue
                _ultimo_log.append(txt)
                if len(_ultimo_log) > 500:
                    _ultimo_log[:] = _ultimo_log[-500:]
            proc.wait()

        await asyncio.get_event_loop().run_in_executor(None, _ler)
    finally:
        _bot_rodando  = False
        _watch_ativo  = False
        _bot_processo = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))


def _db_manager():
    sys.path.insert(0, str(BASE_DIR))
    from modules.database_manager import DatabaseManager
    config = _load_config()
    db_path = config.get("db_path", str(BASE_DIR / "botmercadoi.db"))
    return DatabaseManager(db_path)


# ---------------------------------------------------------------------------
# Endpoints — painel
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html = BASE_DIR / "panel_static" / "index.html"
    return HTMLResponse(html.read_text(encoding="utf-8"))


@app.get("/api/execucoes")
async def listar_execucoes(dias: int = 7):
    try:
        return JSONResponse(_execucoes_db(dias))
    except Exception:
        return JSONResponse(_parse_logs_ultimos_dias(dias))


@app.get("/api/execucoes/{data}")
async def execucoes_do_dia(data: str):
    if not re.match(r"\d{4}-\d{2}-\d{2}", data):
        raise HTTPException(400, "Formato de data inválido. Use YYYY-MM-DD.")
    try:
        return JSONResponse([e for e in _execucoes_db(365) if e.get("data") == data])
    except Exception:
        return JSONResponse(_parse_logs_dia(data))


@app.get("/api/status")
async def status():
    return JSONResponse({
        "rodando":      _bot_rodando,
        "watch_ativo":  _watch_ativo,
        "hoje":         date.today().isoformat(),
        "autenticado":  True,
        "senha_ativa":  bool(_SENHA),
    })


@app.post("/api/processar")
async def processar_agora(background_tasks: BackgroundTasks):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Bot já está rodando"}, status_code=409)
    background_tasks.add_task(_rodar_bot, watch=False)
    return JSONResponse({"ok": True, "msg": "Bot iniciado com sucesso"})


@app.post("/api/watch/iniciar")
async def iniciar_watch(background_tasks: BackgroundTasks):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Bot já está rodando"}, status_code=409)
    cfg = _load_config()
    intervalo = cfg.get("watch_intervalo_minutos", 5)
    background_tasks.add_task(_rodar_bot, watch=True, intervalo=intervalo)
    return JSONResponse({"ok": True, "intervalo": intervalo, "msg": f"Watch mode iniciado ({intervalo} min)"})


@app.post("/api/watch/parar")
async def parar_watch():
    global _bot_processo
    if _bot_processo and _bot_rodando:
        try:
            _bot_processo.terminate()
            return JSONResponse({"ok": True, "msg": "Watch mode encerrado"})
        except Exception as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)
    return JSONResponse({"ok": False, "msg": "Watch mode não está ativo"}, status_code=409)


@app.get("/api/chrome-status")
async def chrome_status():
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=2) as client:
            r = await client.get("http://localhost:9222/json/version")
            if r.status_code == 200:
                info = r.json()
                return JSONResponse({"ok": True, "browser": info.get("Browser", "Chrome")})
    except Exception:
        pass
    return JSONResponse({"ok": False, "msg": "Chrome offline"})


_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\{}\AppData\Local\Google\Chrome\Application\chrome.exe".format(
        os.environ.get("USERNAME", "")
    ),
]


@app.post("/api/abrir-chrome")
async def abrir_chrome():
    import subprocess
    config = _load_config()
    chrome_exe = config.get("chrome_path", "")
    if not chrome_exe or not os.path.exists(chrome_exe):
        chrome_exe = next((p for p in _CHROME_PATHS if os.path.exists(p)), "")
    if not chrome_exe:
        return JSONResponse({"ok": False, "msg": "Chrome não encontrado. Instale o Google Chrome."}, status_code=404)
    profile_path = config.get("mercadoi_profile_path", r"C:\chrome_bot_mercadoi")
    try:
        subprocess.Popen(
            [
                chrome_exe,
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile_path}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return JSONResponse({"ok": True, "msg": "Chrome iniciado"})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/api/logs/live")
async def logs_live(ultimas: int = 80):
    return JSONResponse(_ultimo_log[-ultimas:])


@app.get("/screenshots/{filename}")
async def servir_screenshot(filename: str):
    path = SCREENSHOTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Screenshot não encontrado")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Endpoints — planilha
# ---------------------------------------------------------------------------

@app.get("/api/pendentes")
async def listar_pendentes():
    try:
        sheet = _db_manager()
        items = sheet.listar_pendentes()
        return JSONResponse({
            "count": len(items),
            "items": [{"url": r.get("url_instagram", ""), "linha": r["_row_index"]} for r in items],
        })
    except Exception as e:
        return JSONResponse({"count": 0, "items": [], "error": str(e)})


_INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+", re.IGNORECASE
)


class AdicionarRequest(BaseModel):
    urls: list[str]


@app.post("/api/adicionar")
async def adicionar_url(body: AdicionarRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar antes de adicionar"}, status_code=409)

    urls_raw = [u.strip() for u in body.urls if u.strip()]
    if not urls_raw:
        return JSONResponse({"ok": False, "msg": "Nenhuma URL informada"}, status_code=400)

    try:
        sheet = _db_manager()
        _, rows = sheet._todas_as_linhas()
        existentes = {r.get("url_instagram", "").strip(): r.get("status", "") for r in rows if r.get("url_instagram")}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"Erro ao conectar à planilha: {e}"}, status_code=500)

    results = []
    adicionadas_agora: set[str] = set()

    def _pode_reativar(status: str) -> bool:
        return "erro" in status.lower() or status == "processando"

    for url in urls_raw:
        if not _INSTAGRAM_RE.match(url):
            results.append({"url": url, "status": "invalida", "msg": "URL não é um post do Instagram"})
            continue
        if url in adicionadas_agora:
            results.append({"url": url, "status": "duplicada", "msg": "Duplicada neste envio"})
            continue
        if url in existentes:
            status_existente = existentes[url]
            if _pode_reativar(status_existente):
                try:
                    sheet.resetar_url(url)
                    adicionadas_agora.add(url)
                    results.append({"url": url, "status": "adicionada", "msg": "Reativada para reprocessamento"})
                except Exception as e:
                    results.append({"url": url, "status": "erro", "msg": str(e)})
            else:
                results.append({"url": url, "status": "duplicada", "msg": "Já existe na fila"})
            continue
        try:
            linha = sheet.adicionar_pendente(url)
            adicionadas_agora.add(url)
            results.append({"url": url, "status": "adicionada", "linha": linha, "msg": f"Linha {linha}"})
        except Exception as e:
            results.append({"url": url, "status": "erro", "msg": str(e)})

    return JSONResponse({
        "ok": True,
        "results": results,
        "adicionadas": sum(1 for r in results if r["status"] == "adicionada"),
        "duplicadas":  sum(1 for r in results if r["status"] == "duplicada"),
        "invalidas":   sum(1 for r in results if r["status"] == "invalida"),
        "erros":       sum(1 for r in results if r["status"] == "erro"),
    })


class ReprocessarRequest(BaseModel):
    url: str


@app.post("/api/reprocessar")
async def reprocessar(body: ReprocessarRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    try:
        sheet = _db_manager()
        ok = sheet.resetar_url(body.url)
        if ok:
            return JSONResponse({"ok": True, "msg": "URL redefinida para pendente"})
        return JSONResponse({"ok": False, "msg": "URL não encontrada na planilha"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Endpoints — configuração
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    try:
        cfg = _load_config()
        token   = cfg.get("telegram_bot_token", "").strip()
        chat_id = cfg.get("telegram_chat_id", "").strip()
        return JSONResponse({
            "watch_intervalo_minutos": cfg.get("watch_intervalo_minutos", 5),
            "telegram_configurado":   bool(token and chat_id),
            "telegram_bot_token":     token,
            "telegram_chat_id":       chat_id,
            "senha_configurada":      bool(cfg.get("panel_senha", "").strip()),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class ConfigRequest(BaseModel):
    watch_intervalo_minutos: int | None = None


@app.post("/api/config")
async def update_config(body: ConfigRequest):
    try:
        cfg = _load_config()
        if body.watch_intervalo_minutos is not None:
            if not (1 <= body.watch_intervalo_minutos <= 1440):
                return JSONResponse({"ok": False, "msg": "Intervalo deve ser entre 1 e 1440 minutos"}, status_code=400)
            cfg["watch_intervalo_minutos"] = body.watch_intervalo_minutos
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class SenhaRequest(BaseModel):
    nova_senha: str


@app.post("/api/config/senha")
async def salvar_senha(body: SenhaRequest):
    global _SENHA
    try:
        cfg = _load_config()
        cfg["panel_senha"] = body.nova_senha.strip()
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _SENHA = cfg["panel_senha"]
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class TelegramRequest(BaseModel):
    telegram_bot_token: str
    telegram_chat_id: str


@app.post("/api/config/telegram")
async def salvar_telegram(body: TelegramRequest):
    try:
        cfg = _load_config()
        cfg["telegram_bot_token"] = body.telegram_bot_token.strip()
        cfg["telegram_chat_id"]   = body.telegram_chat_id.strip()
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.post("/api/testar-telegram")
async def testar_telegram():
    try:
        cfg = _load_config()
        from modules.notificador import notificar
        ok = await notificar(cfg, "✅ <b>Bot Mercadoi</b> — Teste de notificação funcionando!")
        if ok:
            return JSONResponse({"ok": True, "msg": "Mensagem enviada com sucesso!"})
        return JSONResponse({"ok": False, "msg": "Falha ao enviar. Verifique o token e chat_id."}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class RemoverRequest(BaseModel):
    id: int


@app.post("/api/remover")
async def remover_item(body: RemoverRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    try:
        db = _db_manager()
        with db._lock:
            with db._conn() as conn:
                cur = conn.execute("DELETE FROM imoveis WHERE id=?", (body.id,))
                conn.commit()
        if cur.rowcount:
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "msg": "Item não encontrado"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/api/fila")
async def listar_fila():
    """Retorna todos os itens pendentes e com erro para exibição na fila."""
    try:
        db = _db_manager()
        _, rows = db._todas_as_linhas()
        fila = [
            {
                "id":          r["_row_index"],
                "url":         r.get("url_instagram", ""),
                "status":      r.get("status", ""),
                "mensagem_erro": r.get("mensagem_erro", ""),
                "criado_em":   r.get("criado_em", ""),
                "atualizado_em": r.get("atualizado_em", ""),
                "mercadoi_url": r.get("mercadoi_url", ""),
            }
            for r in rows
            if r.get("status", "") in ("pendente", "processando")
            or "erro" in r.get("status", "").lower()
        ]
        return JSONResponse({"count": len(fila), "items": fila})
    except Exception as e:
        return JSONResponse({"count": 0, "items": [], "error": str(e)})


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Bot Mercadoi — Painel")
    print("  Acesse: http://localhost:8000")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
