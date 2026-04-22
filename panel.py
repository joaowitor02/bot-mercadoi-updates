"""
Painel web local do Bot Mercadoi.
Acesse http://localhost:8000 após iniciar.
"""

import asyncio
import hashlib
import hmac as _hmac_mod
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

_logger = logging.getLogger("panel")

from version import VERSION

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

_USUARIO: str = ""        # username do cliente
_SENHA: str = ""          # senha do cliente
_ADMIN_USUARIO: str = ""  # username do administrador
_ADMIN_SENHA: str = ""    # senha do administrador
_LICENCA_EXPIRA: str = ""  # "YYYY-MM-DD" ou "" para sem expiração
_SESSION_TOKENS: dict[str, str] = {}  # token -> "admin" | "user"

# Rotas públicas (sem auth)
_PUBLIC_PATHS = {"/login", "/favicon.ico", "/licenca-expirada", "/api/health"}

# Rotas exclusivas do administrador
_ADMIN_API_PATHS = frozenset({
    "/api/config", "/api/config/senha", "/api/config/telegram",
    "/api/config/admin-senha", "/api/config/licenca", "/api/testar-telegram",
    "/api/atualizar", "/api/tunnel/baixar", "/api/config/avancada",
})

# ---------------------------------------------------------------------------
# Tunnel (cloudflared)
# ---------------------------------------------------------------------------

_TUNNEL_URL: str = ""
_TUNNEL_PROCESSO: subprocess.Popen | None = None

CLOUDFLARED_EXE = BASE_DIR / "cloudflared.exe"
_CLOUDFLARED_DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)


# ---------------------------------------------------------------------------
# Segurança: hash de senhas + assinatura HMAC da licença
# ---------------------------------------------------------------------------

_LICENSE_SECRET = b"BotMercadoi@2024#7f3c9a1e"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _is_hash(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", text))


def _verificar_senha(raw: str, stored: str) -> bool:
    if not stored:
        return False
    if _is_hash(stored):
        return _hmac_mod.compare_digest(_sha256(raw), stored)
    return _hmac_mod.compare_digest(raw.encode("utf-8"), stored.encode("utf-8"))


def _assinar_licenca(data_str: str) -> str:
    return _hmac_mod.new(_LICENSE_SECRET, data_str.encode(), hashlib.sha256).hexdigest()


def _licenca_assinatura_valida(data_str: str, sig: str) -> bool:
    if not data_str:
        return True
    if not sig:
        return False
    try:
        return _hmac_mod.compare_digest(_assinar_licenca(data_str), sig)
    except Exception:
        return False


async def _baixar_cloudflared() -> bool:
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(_CLOUDFLARED_DOWNLOAD_URL)
            if r.status_code == 200:
                CLOUDFLARED_EXE.write_bytes(r.content)
                return True
    except Exception as e:
        _logger.warning(f"Falha ao baixar cloudflared: {e}")
    return False



def _tunnel_worker():
    global _TUNNEL_URL, _TUNNEL_PROCESSO
    try:
        proc = subprocess.Popen(
            [str(CLOUDFLARED_EXE), "tunnel", "--url", "http://localhost:8000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        _TUNNEL_PROCESSO = proc
        _url_re = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
        for linha in proc.stdout:
            m = _url_re.search(linha)
            if m and not _TUNNEL_URL:
                _TUNNEL_URL = m.group(0)
                _logger.info(f"Tunnel ativo: {_TUNNEL_URL}")
        proc.wait()
    except Exception as e:
        _logger.warning(f"Tunnel worker encerrado com erro: {e}")
    finally:
        _TUNNEL_URL = ""
        _TUNNEL_PROCESSO = None


def _licenca_expirada() -> bool:
    if not _LICENCA_EXPIRA:
        return False
    try:
        return date.fromisoformat(_LICENCA_EXPIRA) < date.today()
    except Exception:
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get("mercadoi_session", "")
        nivel = _SESSION_TOKENS.get(token, "")

        # Licença expirada bloqueia usuário comum (admin continua)
        if _licenca_expirada() and nivel != "admin":
            if path.startswith("/api/") or path.startswith("/screenshots/"):
                return JSONResponse({"error": "Licença expirada", "expirada": True}, status_code=403)
            return RedirectResponse("/licenca-expirada", status_code=302)

        # Não autenticado
        if not nivel:
            if path.startswith("/api/") or path.startswith("/screenshots/"):
                return JSONResponse({"error": "Não autenticado"}, status_code=401)
            return RedirectResponse("/login", status_code=302)

        # Rotas exclusivas do admin
        if path in _ADMIN_API_PATHS and nivel != "admin":
            return JSONResponse(
                {"error": "Acesso restrito ao administrador", "admin_required": True},
                status_code=403,
            )

        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.on_event("startup")
async def _startup():
    global _USUARIO, _SENHA, _ADMIN_USUARIO, _ADMIN_SENHA, _LICENCA_EXPIRA
    try:
        path = BASE_DIR / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        dirty = False

        panel_usuario = cfg.get("panel_usuario", "").strip()
        panel_senha   = cfg.get("panel_senha",   "").strip()
        admin_usuario = cfg.get("admin_usuario", "").strip()
        admin_senha   = cfg.get("admin_senha",   "").strip()

        # Auto-migrar senhas plaintext → SHA-256
        if panel_senha and not _is_hash(panel_senha):
            panel_senha = _sha256(panel_senha)
            cfg["panel_senha"] = panel_senha
            dirty = True
        if admin_senha and not _is_hash(admin_senha):
            admin_senha = _sha256(admin_senha)
            cfg["admin_senha"] = admin_senha
            dirty = True

        _USUARIO      = panel_usuario
        _SENHA        = panel_senha
        _ADMIN_USUARIO = admin_usuario
        _ADMIN_SENHA  = admin_senha

        # Validar assinatura da licença (impede edição manual do config.json)
        expira = cfg.get("licenca_expira", "").strip()
        sig    = cfg.get("licenca_assinatura", "").strip()
        if expira and not _licenca_assinatura_valida(expira, sig):
            expira = ""
            cfg["licenca_expira"]     = ""
            cfg["licenca_assinatura"] = ""
            dirty = True
        _LICENCA_EXPIRA = expira

        if dirty:
            path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

        # Iniciar tunnel se cloudflared presente ou usar_tunnel=true no config
        if CLOUDFLARED_EXE.exists():
            threading.Thread(target=_tunnel_worker, daemon=True).start()
        elif cfg.get("usar_tunnel", False):
            ok = await _baixar_cloudflared()
            if ok:
                threading.Thread(target=_tunnel_worker, daemon=True).start()
    except Exception as e:
        _logger.error(f"Erro crítico na inicialização do painel: {e}", exc_info=True)


@app.get("/licenca-expirada", response_class=HTMLResponse)
async def licenca_expirada_page():
    html = BASE_DIR / "panel_static" / "licenca_expirada.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1 style='font-family:sans-serif;text-align:center;margin-top:10vh'>"
        "Licença expirada — entre em contato para renovar.</h1>"
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(erro: str = ""):
    html = BASE_DIR / "panel_static" / "login.html"
    content = html.read_text(encoding="utf-8")
    if erro:
        content = content.replace("<!--ERRO-->",
            '<p class="text-sm text-red-500 text-center mt-1">Usuário ou senha incorretos.</p>')
    return HTMLResponse(content)


@app.post("/login")
async def fazer_login(usuario: str = Form(...), senha: str = Form(...)):
    u = usuario.strip().lower()

    # Admin tem prioridade (ignora expiração de licença)
    if _ADMIN_SENHA:
        usuario_ok = (not _ADMIN_USUARIO) or _hmac_mod.compare_digest(u.encode("utf-8"), _ADMIN_USUARIO.lower().encode("utf-8"))
        if usuario_ok and _verificar_senha(senha, _ADMIN_SENHA):
            token = secrets.token_hex(32)
            _SESSION_TOKENS[token] = "admin"
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
            return resp

    # Usuário comum é bloqueado se licença expirou
    if _SENHA:
        usuario_ok = (not _USUARIO) or _hmac_mod.compare_digest(u.encode("utf-8"), _USUARIO.lower().encode("utf-8"))
        if usuario_ok and _verificar_senha(senha, _SENHA):
            if _licenca_expirada():
                return RedirectResponse("/licenca-expirada", status_code=303)
            token = secrets.token_hex(32)
            _SESSION_TOKENS[token] = "user"
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
            return resp

    # Sem credenciais configuradas: qualquer entrada concede acesso de usuário
    if not _SENHA and not _ADMIN_SENHA:
        token = secrets.token_hex(32)
        _SESSION_TOKENS[token] = "user"
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
        return resp

    return RedirectResponse("/login?erro=1", status_code=303)


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("mercadoi_session", "")
    _SESSION_TOKENS.pop(token, None)
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


@app.get("/api/health")
async def health():
    return JSONResponse({"ok": True})


@app.get("/api/status")
async def status(request: Request):
    token = request.cookies.get("mercadoi_session", "")
    sem_auth = not _SENHA and not _ADMIN_SENHA
    nivel = _SESSION_TOKENS.get(token, "admin" if sem_auth else "")
    return JSONResponse({
        "rodando":      _bot_rodando,
        "watch_ativo":  _watch_ativo,
        "hoje":         date.today().isoformat(),
        "autenticado":  bool(nivel),
        "senha_ativa":  bool(_SENHA),
        "nivel":        nivel,  # "admin" | "user" | ""
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
    mercadoi_url = config.get("mercadoi_url", "https://mercadoi.com.br")

    # Encerra instâncias do Chrome que já estejam usando este perfil para evitar conflito
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] and "chrome" in proc.info["name"].lower():
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if profile_path.lower() in cmdline.lower() or "9222" in cmdline:
                        proc.kill()
                        _logger.info(f"Chrome anterior encerrado (pid {proc.info['pid']})")
            except Exception:
                pass
        await asyncio.sleep(1)
    except ImportError:
        pass  # psutil não instalado — tenta abrir mesmo assim

    try:
        subprocess.Popen(
            [
                chrome_exe,
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile_path}",
                "--no-first-run",
                "--no-default-browser-check",
                mercadoi_url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        # Aguarda Chrome ficar acessível (até 10s)
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    r = await client.get("http://localhost:9222/json/version")
                    if r.status_code == 200:
                        return JSONResponse({"ok": True, "msg": "Chrome iniciado e pronto!"})
                except Exception:
                    pass

        return JSONResponse({"ok": True, "msg": "Chrome iniciado — aguarde alguns segundos para ele carregar."})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/api/logs/live")
async def logs_live(ultimas: int = 80):
    return JSONResponse(_ultimo_log[-ultimas:])


@app.get("/screenshots/{filename}")
async def servir_screenshot(filename: str):
    if not re.match(r"^[\w\-]+\.[a-zA-Z]{2,5}$", filename):
        raise HTTPException(400, "Filename inválido")
    path = (SCREENSHOTS_DIR / filename).resolve()
    if not str(path).startswith(str(SCREENSHOTS_DIR.resolve())):
        raise HTTPException(403, "Acesso negado")
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
                _STATUS_PUBLICADO = {"rascunho_salvo", "rascunho_salvo_sem_midia_video"}
                if status_existente in _STATUS_PUBLICADO:
                    msg_dup = "Já publicado com sucesso"
                elif status_existente == "pendente":
                    msg_dup = "Já está na fila aguardando processamento"
                else:
                    msg_dup = "Já existe na fila"
                results.append({"url": url, "status": "duplicada", "msg": msg_dup})
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
        expira  = cfg.get("licenca_expira", "").strip()
        # Dias restantes de licença
        dias_restantes = None
        if expira:
            try:
                dias_restantes = (date.fromisoformat(expira) - date.today()).days
            except Exception:
                pass
        return JSONResponse({
            "watch_intervalo_minutos":  cfg.get("watch_intervalo_minutos", 5),
            "telegram_configurado":     bool(token and chat_id),
            "telegram_bot_token":       token,
            "telegram_chat_id":         chat_id,
            "panel_usuario":            cfg.get("panel_usuario", ""),
            "senha_configurada":        bool(cfg.get("panel_senha", "").strip()),
            "admin_usuario":            cfg.get("admin_usuario", ""),
            "admin_senha_configurada":  bool(cfg.get("admin_senha", "").strip()),
            "licenca_expira":           expira,
            "licenca_dias_restantes":   dias_restantes,
            # campos avançados
            "mercadoi_url":             cfg.get("mercadoi_url", ""),
            "downloads_path":           cfg.get("downloads_path", ""),
            "deepseek_api_key":         cfg.get("deepseek_api_key", ""),
            "deepseek_profile_path":    cfg.get("deepseek_profile_path", ""),
            "mercadoi_profile_path":    cfg.get("mercadoi_profile_path", ""),
            "chrome_path":              cfg.get("chrome_path", ""),
            "version_check_url":        cfg.get("version_check_url", ""),
            "update_zip_url":           cfg.get("update_zip_url", ""),
            "usar_tunnel":              cfg.get("usar_tunnel", False),
            "usar_deepseek_api":        cfg.get("usar_deepseek_api", False),
            "whatsapp_default":         cfg.get("whatsapp_default", ""),
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
    novo_usuario: str = ""
    nova_senha: str = ""


@app.post("/api/config/senha")
async def salvar_senha(body: SenhaRequest):
    global _USUARIO, _SENHA
    try:
        cfg = _load_config()
        novo_usuario = body.novo_usuario.strip().lower()
        nova_senha   = body.nova_senha.strip()
        cfg["panel_usuario"] = novo_usuario
        hashed = _sha256(nova_senha) if nova_senha else ""
        cfg["panel_senha"] = hashed
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _USUARIO = novo_usuario
        _SENHA   = hashed
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


class AdminSenhaRequest(BaseModel):
    novo_usuario: str = ""
    nova_senha: str = ""


@app.post("/api/config/admin-senha")
async def salvar_admin_senha(body: AdminSenhaRequest):
    global _ADMIN_USUARIO, _ADMIN_SENHA
    try:
        cfg = _load_config()
        novo_usuario = body.novo_usuario.strip().lower()
        nova_senha   = body.nova_senha.strip()
        cfg["admin_usuario"] = novo_usuario
        hashed = _sha256(nova_senha) if nova_senha else ""
        cfg["admin_senha"] = hashed
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _ADMIN_USUARIO = novo_usuario
        _ADMIN_SENHA   = hashed
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class LicencaRequest(BaseModel):
    licenca_expira: str  # "YYYY-MM-DD" ou "" para sem expiração


@app.post("/api/config/licenca")
async def salvar_licenca(body: LicencaRequest):
    global _LICENCA_EXPIRA
    try:
        expira = body.licenca_expira.strip()
        if expira:
            date.fromisoformat(expira)  # valida formato
        cfg = _load_config()
        cfg["licenca_expira"]     = expira
        cfg["licenca_assinatura"] = _assinar_licenca(expira) if expira else ""
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _LICENCA_EXPIRA = expira
        return JSONResponse({"ok": True})
    except ValueError:
        return JSONResponse({"ok": False, "msg": "Data inválida. Use AAAA-MM-DD"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class ConfigAvancadaRequest(BaseModel):
    mercadoi_url:          str | None = None
    downloads_path:        str | None = None
    deepseek_api_key:      str | None = None
    deepseek_profile_path: str | None = None
    mercadoi_profile_path: str | None = None
    chrome_path:           str | None = None
    version_check_url:     str | None = None
    update_zip_url:        str | None = None
    usar_tunnel:           bool | None = None
    usar_deepseek_api:     bool | None = None
    whatsapp_default:      str | None = None


@app.post("/api/config/avancada")
async def salvar_config_avancada(body: ConfigAvancadaRequest):
    try:
        cfg = _load_config()
        campos = body.model_dump(exclude_none=True)
        # Sanitizar strings
        for k, v in campos.items():
            if isinstance(v, str):
                campos[k] = v.strip()
        cfg.update(campos)
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


@app.post("/api/limpar-pendentes")
async def limpar_pendentes():
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    try:
        db = _db_manager()
        count = db.limpar_pendentes()
        return JSONResponse({"ok": True, "msg": f"{count} item(ns) removido(s)"})
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
                "id":            r["_row_index"],
                "url":           r.get("url_instagram", ""),
                "status":        r.get("status", ""),
                "mensagem_erro": r.get("mensagem_erro", ""),
                "tentativas":    r.get("tentativas", 0),
                "criado_em":     r.get("criado_em", ""),
                "atualizado_em": r.get("atualizado_em", ""),
                "mercadoi_url":  r.get("mercadoi_url", ""),
            }
            for r in rows
            if r.get("status", "") in ("pendente", "processando")
            or "erro" in r.get("status", "").lower()
        ]
        return JSONResponse({"count": len(fila), "items": fila})
    except Exception as e:
        return JSONResponse({"count": 0, "items": [], "error": str(e)})


# ---------------------------------------------------------------------------
# Endpoints — tunnel
# ---------------------------------------------------------------------------

@app.get("/api/tunnel")
async def get_tunnel():
    return JSONResponse({
        "url":                  _TUNNEL_URL,
        "ativo":                bool(_TUNNEL_URL),
        "cloudflared_presente": CLOUDFLARED_EXE.exists(),
    })


@app.post("/api/tunnel/baixar")
async def baixar_cloudflared_endpoint():
    if CLOUDFLARED_EXE.exists():
        if not _TUNNEL_URL:
            threading.Thread(target=_tunnel_worker, daemon=True).start()
        return JSONResponse({"ok": True, "msg": "cloudflared já está presente. Tunnel iniciando..."})
    ok = await _baixar_cloudflared()
    if ok:
        threading.Thread(target=_tunnel_worker, daemon=True).start()
        return JSONResponse({"ok": True, "msg": "cloudflared baixado. Tunnel iniciando — aguarde ~10s."})
    return JSONResponse({"ok": False, "msg": "Falha ao baixar cloudflared. Verifique a conexão."}, status_code=500)


# ---------------------------------------------------------------------------
# Endpoints — versão e atualização
# ---------------------------------------------------------------------------

@app.get("/api/version")
async def get_version():
    try:
        cfg = _load_config()
        check_url = cfg.get("version_check_url", "").strip()
    except Exception:
        check_url = ""

    versao_remota = None
    has_update    = False
    changelog     = ""

    if check_url:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.get(check_url)
                if r.status_code == 200:
                    m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', r.text)
                    if m:
                        versao_remota = m.group(1)
                        has_update    = versao_remota != VERSION

                # Busca CHANGELOG.md na mesma base da version_check_url
                if has_update:
                    changelog_url = re.sub(r'[^/]+$', 'CHANGELOG.md', check_url)
                    try:
                        rc = await client.get(changelog_url)
                        if rc.status_code == 200:
                            changelog = rc.text[:6000]  # limita tamanho
                    except Exception:
                        pass
        except Exception:
            pass

    return JSONResponse({
        "versao_atual":  VERSION,
        "versao_remota": versao_remota,
        "has_update":    has_update,
        "changelog":     changelog,
    })


# Arquivos e pastas que nunca devem ser sobrescritos na atualização
_PRESERVAR_ARQUIVOS = frozenset({
    "config.json", "botmercadoi.db", "botmercadoi.db-shm",
    "botmercadoi.db-wal", ".installed", "credentials.json", "cloudflared.exe",
})
_PRESERVAR_PASTAS = frozenset({"logs", ".git", ".claude", "__pycache__"})


@app.post("/api/atualizar")
async def atualizar_bot():
    """Baixa o zip da nova versão e extrai sobre os arquivos atuais."""
    try:
        cfg = _load_config()
        zip_url = cfg.get("update_zip_url", "").strip()
        if not zip_url:
            return JSONResponse(
                {"ok": False, "msg": "update_zip_url não configurado em Config Avançada."},
                status_code=400,
            )

        import httpx as _httpx
        import zipfile
        import tempfile
        import shutil

        async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(zip_url)
            if r.status_code != 200:
                return JSONResponse(
                    {"ok": False, "msg": f"Falha ao baixar atualização (HTTP {r.status_code})."},
                    status_code=500,
                )

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "update.zip")
            with open(zip_path, "wb") as f:
                f.write(r.content)

            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tmp)

            # GitHub cria subpasta REPO-main/ ao exportar zip
            subdirs = [
                d for d in os.listdir(tmp)
                if os.path.isdir(os.path.join(tmp, d)) and d != "__MACOSX"
            ]
            src_dir = os.path.join(tmp, subdirs[0]) if subdirs else tmp

            copiados = 0
            for root, dirs, files in os.walk(src_dir):
                dirs[:] = [d for d in dirs if d not in _PRESERVAR_PASTAS]
                rel_root = os.path.relpath(root, src_dir)
                for fname in files:
                    if fname in _PRESERVAR_ARQUIVOS:
                        continue
                    src = os.path.join(root, fname)
                    if rel_root == ".":
                        dst_dir = BASE_DIR
                    else:
                        dst_dir = BASE_DIR / rel_root
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst_dir / fname)
                    copiados += 1

        # Apaga flag .installed para forçar reinstalação das dependências
        flag = BASE_DIR / ".installed"
        if flag.exists():
            flag.unlink()

        _logger.info(f"Atualização aplicada: {copiados} arquivo(s) substituído(s)")
        return JSONResponse({
            "ok":  True,
            "msg": f"✅ Atualização aplicada ({copiados} arquivos).\n\nFeche e abra novamente o painel (Abrir Painel.vbs) para concluir.",
        })

    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "msg": "Arquivo baixado não é um zip válido."}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Bot Mercadoi — Painel")
    print("  Acesse: http://localhost:8000")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
