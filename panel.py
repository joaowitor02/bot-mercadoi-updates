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
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_logger = logging.getLogger("panel")

from version import VERSION

print(f"[INICIO] Bot Mercadoi v{VERSION} — carregando...", flush=True)
print(f"[INICIO] Python {sys.version.split()[0]} em {sys.platform}", flush=True)

# Python <= 3.13 no Windows: ProactorEventLoop gera ValueError em transports fechados.
# SelectorEventLoop evita isso. Em 3.14+ a política foi depreciada e não é necessária.
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from modules.olx_scraper import normalizar_url as normalizar_olx_url, url_valida as olx_url_valida
from modules.orulo_scraper import normalizar_url as normalizar_orulo_url, url_valida as orulo_url_valida

app = FastAPI(title="Bot Mercadoi — Painel")

BASE_DIR = Path(__file__).parent
# Em Docker, BOT_DATA_DIR aponta para o volume persistente (/data)
# Em Windows local, usa a pasta do projeto normalmente
_DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", str(BASE_DIR)))
LOGS_DIR = _DATA_DIR / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_USUARIO: str = ""        # username do cliente
_SENHA: str = ""          # senha do cliente
_ADMIN_USUARIO: str = ""  # username do administrador
_ADMIN_SENHA: str = ""    # senha do administrador
_LICENCA_EXPIRA: str = ""  # "YYYY-MM-DD" ou "" para sem expiração

# token -> {"nivel": "admin"|"user", "exp": timestamp}
_SESSION_TOKENS: dict[str, dict] = {}
_SESSION_TTL = 30 * 24 * 3600  # 30 dias

# Proteção brute-force: ip -> [timestamps de falhas]
_LOGIN_FALHAS: dict[str, list] = {}
_LOGIN_MAX_FALHAS = 10
_LOGIN_JANELA_SEG = 600   # 10 min
_LOGIN_BLOQUEIO_SEG = 600  # 10 min de bloqueio


def _get_session_nivel(token: str) -> str:
    entry = _SESSION_TOKENS.get(token)
    if not entry:
        return ""
    if entry["exp"] < time.time():
        _SESSION_TOKENS.pop(token, None)
        return ""
    return entry["nivel"]


def _registrar_falha_login(ip: str) -> bool:
    """Registra falha e retorna True se o IP deve ser bloqueado."""
    agora = time.time()
    falhas = [t for t in _LOGIN_FALHAS.get(ip, []) if agora - t < _LOGIN_JANELA_SEG]
    falhas.append(agora)
    _LOGIN_FALHAS[ip] = falhas
    return len(falhas) >= _LOGIN_MAX_FALHAS


def _ip_bloqueado(ip: str) -> bool:
    agora = time.time()
    falhas = [t for t in _LOGIN_FALHAS.get(ip, []) if agora - t < _LOGIN_BLOQUEIO_SEG]
    _LOGIN_FALHAS[ip] = falhas
    return len(falhas) >= _LOGIN_MAX_FALHAS

# Rotas públicas (sem auth)
_PUBLIC_PATHS = {"/login", "/favicon.ico", "/licenca-expirada", "/api/health", "/primeiro-acesso"}

# Rotas exclusivas do administrador
_ADMIN_API_PATHS = frozenset({
    "/api/config", "/api/config/senha", "/api/config/telegram",
    "/api/config/admin-senha", "/api/config/licenca", "/api/testar-telegram",
    "/api/tunnel/baixar", "/api/config/avancada", "/api/gerar-licenca",
    "/api/config/wordpress", "/api/diagnostico", "/api/limpar-arquivos-antigos",
    # /api/atualizar e /api/conectar-wordpress são intencionais fora daqui
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

_LICENSE_SECRET = os.environ.get("LICENSE_SECRET", "BotMercadoi@2024#7f3c9a1e").encode()


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

        # Sem senhas configuradas → obriga setup inicial
        if not _SENHA and not _ADMIN_SENHA:
            if path.startswith("/api/") or path.startswith("/screenshots/"):
                return JSONResponse({"error": "Configure o acesso inicial primeiro"}, status_code=503)
            return RedirectResponse("/primeiro-acesso", status_code=302)

        token = request.cookies.get("mercadoi_session", "")
        nivel = _get_session_nivel(token)

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


class RenovarLicencaRequest(BaseModel):
    chave: str  # formato: "YYYY-MM-DD:HMAC_HEX"


@app.post("/api/renovar-licenca")
async def renovar_licenca(body: RenovarLicencaRequest):
    """Permite ao cliente renovar a licença colando a chave enviada pelo admin."""
    global _LICENCA_EXPIRA
    try:
        chave = body.chave.strip()
        partes = chave.split(":")
        if len(partes) != 2:
            return JSONResponse({"ok": False, "msg": "Chave inválida."}, status_code=400)
        expira, sig = partes[0].strip(), partes[1].strip()
        date.fromisoformat(expira)
        if not _licenca_assinatura_valida(expira, sig):
            return JSONResponse({"ok": False, "msg": "Chave inválida ou adulterada."}, status_code=400)
        cfg = _load_config()
        cfg["licenca_expira"]     = expira
        cfg["licenca_assinatura"] = sig
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _LICENCA_EXPIRA = expira
        dias = (date.fromisoformat(expira) - date.today()).days
        return JSONResponse({"ok": True, "msg": f"Licença renovada até {expira} ({dias} dias)."})
    except ValueError:
        return JSONResponse({"ok": False, "msg": "Data inválida na chave."}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/login", response_class=HTMLResponse)
async def login_page(erro: str = ""):
    if not _SENHA and not _ADMIN_SENHA:
        return RedirectResponse("/primeiro-acesso", status_code=302)
    html = BASE_DIR / "panel_static" / "login.html"
    content = html.read_text(encoding="utf-8")
    if erro:
        content = content.replace("<!--ERRO-->",
            '<p class="text-sm text-red-500 text-center mt-1">Usuário ou senha incorretos.</p>')
    return HTMLResponse(content)


@app.post("/login")
async def fazer_login(request: Request, usuario: str = Form(...), senha: str = Form(...)):
    ip = request.client.host if request.client else "unknown"

    if _ip_bloqueado(ip):
        return RedirectResponse("/login?erro=1", status_code=303)

    u = usuario.strip().lower()

    # Admin tem prioridade (ignora expiração de licença)
    if _ADMIN_SENHA:
        usuario_ok = (not _ADMIN_USUARIO) or _hmac_mod.compare_digest(u.encode("utf-8"), _ADMIN_USUARIO.lower().encode("utf-8"))
        if usuario_ok and _verificar_senha(senha, _ADMIN_SENHA):
            _LOGIN_FALHAS.pop(ip, None)
            token = secrets.token_hex(32)
            _SESSION_TOKENS[token] = {"nivel": "admin", "exp": time.time() + _SESSION_TTL}
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
            return resp

    # Usuário comum é bloqueado se licença expirou
    if _SENHA:
        usuario_ok = (not _USUARIO) or _hmac_mod.compare_digest(u.encode("utf-8"), _USUARIO.lower().encode("utf-8"))
        if usuario_ok and _verificar_senha(senha, _SENHA):
            if _licenca_expirada():
                return RedirectResponse("/licenca-expirada", status_code=303)
            _LOGIN_FALHAS.pop(ip, None)
            token = secrets.token_hex(32)
            _SESSION_TOKENS[token] = {"nivel": "user", "exp": time.time() + _SESSION_TTL}
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("mercadoi_session", token, httponly=True, samesite="strict")
            return resp

    # Sem credenciais configuradas: redireciona para setup inicial
    if not _SENHA and not _ADMIN_SENHA:
        return RedirectResponse("/primeiro-acesso", status_code=303)

    _registrar_falha_login(ip)
    return RedirectResponse("/login?erro=1", status_code=303)


@app.get("/primeiro-acesso", response_class=HTMLResponse)
async def primeiro_acesso_page(erro: str = ""):
    if _SENHA or _ADMIN_SENHA:
        return RedirectResponse("/login", status_code=302)
    html = BASE_DIR / "panel_static" / "primeiro_acesso.html"
    content = html.read_text(encoding="utf-8")
    msgs = {
        "senhas": "As senhas não coincidem.",
        "curta":  "A senha deve ter pelo menos 6 caracteres.",
        "vazio":  "Preencha todos os campos.",
    }
    msg = msgs.get(erro, "")
    if msg:
        content = content.replace("<!--ERRO-->",
            f'<p class="text-sm text-red-500 text-center mt-1">{msg}</p>')
    from version import VERSION
    content = content.replace("<!--VER-->", VERSION)
    return HTMLResponse(content)


@app.post("/primeiro-acesso")
async def fazer_primeiro_acesso(
    usuario: str = Form(...), senha: str = Form(...), confirmar: str = Form(...)
):
    global _USUARIO, _SENHA
    if not usuario.strip() or not senha:
        return RedirectResponse("/primeiro-acesso?erro=vazio", status_code=303)
    if len(senha) < 6:
        return RedirectResponse("/primeiro-acesso?erro=curta", status_code=303)
    if senha != confirmar:
        return RedirectResponse("/primeiro-acesso?erro=senhas", status_code=303)

    hashed = _sha256(senha)
    path = BASE_DIR / "config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["panel_usuario"] = usuario.strip()
    cfg["panel_senha"]   = hashed
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    _USUARIO = usuario.strip()
    _SENHA   = hashed
    return RedirectResponse("/login", status_code=303)


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
        if status_raw in ("rascunho_salvo", "rascunho_salvo_sem_midia_video", "publicado"):
            status = "sucesso"
        elif status_raw in ("processando", "capturando", "baixando_midia", "enviando_wp"):
            status = status_raw
        elif "erro" in status_raw:
            status = "falha"
        else:
            status = status_raw or "pendente"

        inicio = (r.get("criado_em") or "")[11:19]
        fim_hora = fim[11:19] if "T" in fim else (fim[11:19] if len(fim) >= 19 else "")
        item = {
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
            "url_publica": r.get("url_publica", ""),
            "origem": r.get("origem", "") or _fonte(r.get("url_instagram", "")),
            "tempo_seg": int(r.get("tempo_seg") or 0),
            "captura_seg": int(r.get("captura_seg") or 0),
            "midia_seg": int(r.get("midia_seg") or 0),
            "publicacao_seg": int(r.get("publicacao_seg") or 0),
        }

        mercadoi_links = [u.strip() for u in (item["mercadoi_url"] or "").split("|") if u.strip()]
        publica_links = [u.strip() for u in (item["url_publica"] or "").split("|") if u.strip()]
        if status == "sucesso" and (len(mercadoi_links) > 1 or len(publica_links) > 1):
            total_links = max(len(mercadoi_links), len(publica_links))
            for idx in range(total_links):
                extra = dict(item)
                extra["execution_id"] = f"{item['execution_id']}-{idx + 1}"
                extra["mercadoi_url"] = mercadoi_links[idx] if idx < len(mercadoi_links) else ""
                extra["url_publica"] = publica_links[idx] if idx < len(publica_links) else ""
                itens.append(extra)
        else:
            itens.append(item)
    itens.sort(key=lambda x: (x.get("data") or "", x.get("fim") or x.get("inicio") or "", x.get("row_id") or 0), reverse=True)
    return itens


# ---------------------------------------------------------------------------
# Estado do bot
# ---------------------------------------------------------------------------

_bot_rodando  = False
_watch_ativo  = False
_bot_processo = None
_bot_task     = None
_ultimo_log: list[str] = []
_ultimo_inicio_bot = 0.0


def _bot_em_execucao() -> bool:
    if _bot_processo is not None:
        try:
            if _bot_processo.poll() is None:
                return True
        except Exception:
            pass
    if _bot_task is not None and not _bot_task.done():
        return True
    if _bot_rodando:
        return True
    # Detecta main.py rodando via systemd ou outro lançador externo
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "main.py" in cmd and "panel.py" not in cmd:
                return True
    except Exception:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "main.py"], stderr=subprocess.DEVNULL
            )
            if out.strip():
                return True
        except Exception:
            pass
    return False


def _log_painel(msg: str) -> None:
    linha = f"[painel] {msg}"
    _ultimo_log.append(linha)
    if len(_ultimo_log) > 500:
        _ultimo_log[:] = _ultimo_log[-500:]


def _tail_text_lines(path: Path, limit: int, max_bytes: int = 256 * 1024) -> list[str]:
    """Le somente o fim de arquivos de log grandes."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _filtrar_logs_execucao(linhas: list[str], limit: int) -> list[str]:
    # Sem filtro por timestamp: comparação entre VPS-UTC e logs em Brasília (UTC-3)
    # causava que todas as linhas falhassem e o log parecia travado.
    # O _mesclar_logs_recentes já cuida de duplicatas com _ultimo_log (stdout).
    if not _ultimo_inicio_bot or not _bot_em_execucao():
        return linhas[-limit:]
    # Filtra apenas usando timezone de Brasília para comparação correta
    from datetime import timezone, timedelta
    tz_br = timezone(timedelta(hours=-3))
    corte = datetime.fromtimestamp(max(0, _ultimo_inicio_bot - 3), tz=tz_br)
    filtradas: list[str] = []
    for linha in linhas:
        m = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", linha)
        if not m:
            filtradas.append(linha)  # linha sem timestamp: inclui
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_br)
            if ts >= corte:
                filtradas.append(linha)
        except ValueError:
            filtradas.append(linha)
    return (filtradas or linhas)[-limit:]


def _mesclar_logs_recentes(linhas_arquivo: list[str], limit: int) -> list[str]:
    """Mescla arquivo + stdout do processo sem repetir linhas iguais em sequencia."""
    combinadas = linhas_arquivo + _ultimo_log
    if not combinadas:
        return []
    resultado: list[str] = []
    for linha in combinadas:
        if not linha or (resultado and resultado[-1] == linha):
            continue
        resultado.append(linha)
    return resultado[-limit:]


async def _rodar_bot(watch: bool = False, intervalo: int = 5):
    global _bot_rodando, _watch_ativo, _bot_processo, _ultimo_log, _ultimo_inicio_bot
    _bot_rodando = True
    _watch_ativo = watch
    _ultimo_log  = []
    _ultimo_inicio_bot = time.time()
    _log_painel("bot iniciado em modo watch" if watch else "bot iniciado")
    try:
        args = [sys.executable, "main.py"]
        if watch:
            args += ["--watch", str(intervalo)]
        env = os.environ.copy()
        env["NODE_NO_WARNINGS"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["BOT_DATA_DIR"] = str(_DATA_DIR)
        proc = subprocess.Popen(
            args,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
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
            _log_painel(f"processo finalizado com codigo {proc.returncode}")

        await asyncio.get_event_loop().run_in_executor(None, _ler)
    except Exception as e:
        _log_painel(f"erro ao iniciar/executar bot: {e}")
        _logger.error(f"Erro ao executar bot: {e}", exc_info=True)
    finally:
        _bot_rodando  = False
        _watch_ativo  = False
        _bot_processo = None


def _iniciar_bot_task(watch: bool = False, intervalo: int = 5) -> tuple[bool, str]:
    global _bot_task, _bot_rodando, _watch_ativo, _ultimo_log, _ultimo_inicio_bot
    if _bot_em_execucao():
        return False, "Bot já está rodando"
    _bot_rodando = True
    _watch_ativo = watch
    _ultimo_log = []
    _ultimo_inicio_bot = time.time()
    _log_painel("solicitando inicio do bot")
    _bot_task = asyncio.create_task(_rodar_bot(watch=watch, intervalo=intervalo))
    return True, "Bot iniciado com sucesso"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))


def _save_config(cfg: dict) -> None:
    (BASE_DIR / "config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _db_manager():
    sys.path.insert(0, str(BASE_DIR))
    from modules.database_manager import DatabaseManager
    config = _load_config()
    db_path = config.get("db_path", str(_DATA_DIR / "botmercadoi.db"))
    return DatabaseManager(db_path)


# ---------------------------------------------------------------------------
# Endpoints — painel
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html = BASE_DIR / "panel_static" / "index.html"
    return HTMLResponse(
        html.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


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
    nivel = "admin" if sem_auth else _get_session_nivel(token)

    tempo_medio    = None
    workers_ativos = 0
    try:
        db = _db_manager()
        with db._conn() as conn:
            row = conn.execute(
                "SELECT AVG(tempo_seg) as media FROM ("
                "SELECT tempo_seg FROM imoveis "
                "WHERE tempo_seg > 0 AND status IN "
                "('rascunho_salvo','rascunho_salvo_sem_midia_video','publicado') "
                "ORDER BY id DESC LIMIT 20"
                ")"
            ).fetchone()
            if row and row["media"]:
                tempo_medio = round(row["media"])
            w = conn.execute(
                "SELECT COUNT(*) as c FROM imoveis "
                "WHERE status IN ('processando','capturando','baixando_midia','enviando_wp')"
            ).fetchone()
            workers_ativos = w["c"] if w else 0
    except Exception:
        pass

    rodando_real = _bot_em_execucao()
    # Login Mercadoi ativo
    login_ativo = ""
    try:
        from datetime import datetime as _dt_local, timezone as _tz_local, timedelta as _td_local
        _brasilia = _tz_local(_td_local(hours=-3))
        hoje_br = _dt_local.now(_brasilia).date()
        cfg_l = _load_config()
        logins = cfg_l.get("mercadoi_logins", [])
        admin_u = cfg_l.get("wordpress_xmlrpc_user", "")
        manual = cfg_l.get("mercadoi_login_manual")
        if manual is not None and isinstance(manual, str) and manual == admin_u:
            login_ativo = admin_u  # login admin selecionado manualmente
        elif logins:
            if manual is not None:
                if isinstance(manual, int):
                    idx = int(manual) % len(logins)
                else:
                    _m = re.match(r'^[Mm](\d+)$', str(manual).strip())
                    if _m:
                        idx = (int(_m.group(1)) - 1) % len(logins)
                    else:
                        idx = next((i for i, l in enumerate(logins) if l.get("usuario") == manual), 0)
            else:
                idx = hoje_br.toordinal() % len(logins)
            login_ativo = logins[idx].get("usuario", "")
    except Exception:
        pass
    return JSONResponse({
        "rodando":        rodando_real,
        "watch_ativo":    _watch_ativo,
        "hoje":           date.today().isoformat(),
        "autenticado":    bool(nivel),
        "senha_ativa":    bool(_SENHA),
        "nivel":          nivel,
        "tempo_medio":    tempo_medio,
        "workers_ativos": workers_ativos,
        "login_ativo":    login_ativo,
    })


@app.get("/api/logins")
async def listar_logins(request: Request):
    from datetime import datetime as _dt_l, timezone as _tz_l, timedelta as _td_l
    hoje_br_l = _dt_l.now(_tz_l(_td_l(hours=-3))).date()
    token = request.cookies.get("mercadoi_session", "")
    sem_auth = not _SENHA and not _ADMIN_SENHA
    nivel = "admin" if sem_auth else _get_session_nivel(token)

    cfg = _load_config()
    logins = cfg.get("mercadoi_logins", [])
    admin_user = cfg.get("wordpress_xmlrpc_user", "")
    manual = cfg.get("mercadoi_login_manual")
    auto_idx = (hoje_br_l.toordinal() % len(logins)) if logins else -1

    stats: dict[str, int] = {}
    try:
        db = _db_manager()
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT mercadoi_usuario, COUNT(*) as n FROM imoveis "
                "WHERE status IN ('sucesso','rascunho_salvo','publicado','rascunho_salvo_sem_midia_video') "
                "AND mercadoi_usuario != '' GROUP BY mercadoi_usuario"
            ).fetchall()
            for r in rows:
                stats[r["mercadoi_usuario"]] = r["n"]
    except Exception:
        pass

    resultado = []
    # Login admin: visível apenas para administrador, fora da rotação
    if nivel == "admin" and admin_user:
        is_admin_ativo = (manual == admin_user)
        resultado.append({
            "idx": -1,
            "usuario": admin_user,
            "ativo": is_admin_ativo,
            "manual": is_admin_ativo,
            "auto_hoje": False,
            "cadastros": stats.get(admin_user, 0),
            "admin_login": True,
        })

    # Resolve índice "M17" → 16
    _manual_idx = None
    if manual is not None:
        _m = re.match(r'^[Mm](\d+)$', str(manual).strip())
        if _m:
            _manual_idx = (int(_m.group(1)) - 1) % len(logins) if logins else None

    for i, login in enumerate(logins):
        u = login.get("usuario", "")
        is_manual = (manual == u or manual == i or (_manual_idx is not None and i == _manual_idx))
        is_auto_hoje = (i == auto_idx and manual is None)
        resultado.append({
            "idx": i,
            "usuario": u,
            "ativo": is_manual or is_auto_hoje,
            "manual": is_manual,
            "auto_hoje": is_auto_hoje,
            "cadastros": stats.get(u, 0),
            "admin_login": False,
        })
    return JSONResponse(resultado)


@app.post("/api/logins/selecionar")
async def selecionar_login(request: Request):
    token = request.cookies.get("mercadoi_session", "")
    sem_auth = not _SENHA and not _ADMIN_SENHA
    nivel = "admin" if sem_auth else _get_session_nivel(token)

    try:
        body = await request.json()
        usuario = str(body.get("usuario", "")).strip()
    except Exception:
        return JSONResponse({"ok": False, "msg": "Dados inválidos"}, status_code=400)

    if not usuario:
        return JSONResponse({"ok": False, "msg": "Usuário não informado"}, status_code=400)

    cfg = _load_config()
    logins = cfg.get("mercadoi_logins", [])
    admin_user = cfg.get("wordpress_xmlrpc_user", "")

    # Login admin: só admin pode selecionar
    if usuario == admin_user:
        if nivel != "admin":
            return JSONResponse({"ok": False, "msg": "Acesso restrito ao administrador"}, status_code=403)
        cfg["mercadoi_login_manual"] = usuario
        _save_config(cfg)
        return JSONResponse({"ok": True, "msg": f"Login admin '{usuario}' selecionado"})

    if not any(l.get("usuario") == usuario for l in logins):
        return JSONResponse({"ok": False, "msg": "Login não encontrado"}, status_code=404)
    cfg["mercadoi_login_manual"] = usuario
    _save_config(cfg)
    return JSONResponse({"ok": True, "msg": f"Login '{usuario}' selecionado"})


@app.delete("/api/logins/manual")
async def resetar_login_manual():
    cfg = _load_config()
    cfg["mercadoi_login_manual"] = None
    _save_config(cfg)
    return JSONResponse({"ok": True, "msg": "Voltando à rotação automática diária"})


@app.post("/api/processar")
async def processar_agora():
    ok, msg = _iniciar_bot_task(watch=False)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg}, status_code=409)
    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/watch/iniciar")
async def iniciar_watch():
    if _bot_em_execucao():
        return JSONResponse({"ok": False, "msg": "Bot já está rodando"}, status_code=409)
    cfg = _load_config()
    intervalo = cfg.get("watch_intervalo_minutos", 5)
    ok, msg = _iniciar_bot_task(watch=True, intervalo=intervalo)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg}, status_code=409)
    return JSONResponse({"ok": True, "intervalo": intervalo, "msg": f"Watch mode iniciado ({intervalo} min)"})


@app.post("/api/watch/parar")
async def parar_watch():
    global _bot_processo
    if _bot_processo and _bot_em_execucao():
        try:
            _bot_processo.terminate()
            return JSONResponse({"ok": True, "msg": "Watch mode encerrado"})
        except Exception as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)
    return JSONResponse({"ok": False, "msg": "Watch mode não está ativo"}, status_code=409)


@app.post("/api/parar")
async def parar_bot():
    global _bot_processo
    if _bot_processo and _bot_em_execucao():
        try:
            _bot_processo.terminate()
            return JSONResponse({"ok": True, "msg": "Bot encerrado"})
        except Exception as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)
    return JSONResponse({"ok": False, "msg": "Bot não está rodando"}, status_code=409)


@app.get("/api/chrome-status")
async def chrome_status():
    if sys.platform != "win32":
        return JSONResponse({
            "ok": True,
            "browser": "VPS: Chromium headless gerenciado pelo bot",
            "managed": True,
        })

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
    if sys.platform != "win32":
        return JSONResponse({
            "ok": True,
            "msg": "No VPS o Chromium headless e aberto automaticamente durante o cadastro.",
        })

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
    # Lê sempre do arquivo de log — o FileHandler faz flush() após cada linha,
    # então o arquivo está sempre atualizado. Isso é mais confiável do que o
    # stdout piped no Windows, que sofre de block-buffering mesmo com PYTHONUNBUFFERED.
    from datetime import datetime as _dtl, timezone as _tzl, timedelta as _tdl
    _br = _tzl(_tdl(hours=-3))
    hoje = _dtl.now(_br).strftime("%Y-%m-%d")
    # Inclui também o dia anterior para cobrir logs que cruzaram meia-noite
    ontem = (_dtl.now(_br) - _tdl(days=1)).strftime("%Y-%m-%d")
    candidatos = [
        LOGS_DIR / f"{hoje}.log", BASE_DIR / "logs" / f"{hoje}.log",
        LOGS_DIR / f"{ontem}.log", BASE_DIR / "logs" / f"{ontem}.log",
    ]
    limit = max(1, min(ultimas, 300))
    linhas_arquivo: list[str] = []
    caminhos_lidos: set[Path] = set()
    for log_file in candidatos:
        try:
            log_path = log_file.resolve()
        except Exception:
            log_path = log_file
        if log_path in caminhos_lidos or not log_file.exists():
            continue
        caminhos_lidos.add(log_path)
        try:
            linhas = _tail_text_lines(log_file, limit, max_bytes=512 * 1024)
            linhas_arquivo.extend(_filtrar_logs_execucao(linhas, limit))
        except Exception:
            pass
    linhas = _mesclar_logs_recentes(linhas_arquivo, limit)
    return JSONResponse(linhas, headers={"Cache-Control": "no-store"})


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
_OLX_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)?olx\.com\.br(?:/|$)", re.IGNORECASE
)
_URL_TOKEN_RE = re.compile(
    r"(https?://[^\s<>\]\)\"']+|(?:www\.)?(?:instagram\.com|[\w-]+\.)?olx\.com\.br[^\s<>\]\)\"']*|(?:www\.)?orulo\.com\.br[^\s<>\]\)\"']*)",
    re.IGNORECASE,
)
_PHONE_BR_RE = re.compile(
    r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9[\s.-]*)?\d{4}[\s.-]?\d{4}"
)

def _url_suportada(url: str) -> bool:
    return bool(_INSTAGRAM_RE.match(url) or olx_url_valida(url) or orulo_url_valida(url))

def _normalizar_url_entrada(url: str) -> str:
    url = normalizar_olx_url(url.strip())
    if orulo_url_valida(url):
        return normalizar_orulo_url(url)
    return url

def _extrair_urls_texto(texto: str) -> list[str]:
    urls = []
    for match in _URL_TOKEN_RE.findall(texto or ""):
        url = match.strip().rstrip(".,;")
        if url and not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"
        if url:
            urls.append(_normalizar_url_entrada(url))
    return urls

def _normalizar_whatsapp_contato(texto: str) -> str:
    texto_sem_urls = _URL_TOKEN_RE.sub(" ", texto or "")
    for candidato in _PHONE_BR_RE.findall(texto_sem_urls):
        digitos = re.sub(r"\D", "", candidato)
        if len(digitos) in (10, 11):
            digitos = f"55{digitos}"
        if digitos.startswith("55") and len(digitos) in (12, 13):
            return f"https://wa.me/{digitos}"
    return ""

def _extrair_entradas_adicionar(linhas: list[str]) -> list[dict]:
    entradas = []
    whatsapp_pendente = ""

    for linha in linhas:
        texto = (linha or "").strip()
        if not texto:
            continue

        urls = _extrair_urls_texto(texto)
        whatsapp_linha = _normalizar_whatsapp_contato(texto)

        if urls:
            for url in urls:
                contato = ""
                if olx_url_valida(url):
                    contato = whatsapp_linha or whatsapp_pendente
                    if contato:
                        whatsapp_pendente = ""
                entradas.append({"url": url, "whatsapp_contato": contato})
            continue

        if whatsapp_linha:
            for entrada in reversed(entradas):
                if olx_url_valida(entrada.get("url", "")) and not entrada.get("whatsapp_contato"):
                    entrada["whatsapp_contato"] = whatsapp_linha
                    break
            else:
                whatsapp_pendente = whatsapp_linha

    return entradas

def _fonte(url: str) -> str:
    if _OLX_RE.match(url):
        return "OLX"
    if orulo_url_valida(url):
        return "Órulo"
    return "Instagram"


class AdicionarRequest(BaseModel):
    urls: list[str]
    forcar: bool = False


@app.get("/api/limite-links")
async def get_limite_links(request: Request):
    cfg = _load_config()
    limite = int(cfg.get("limite_links_teste", 0) or 0)
    if not limite:
        return JSONResponse({"limite": 0, "total": 0, "restantes": None, "atingido": False})
    try:
        db = _db_manager()
        with db._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM imoveis").fetchone()[0]
    except Exception:
        total = 0
    return JSONResponse({
        "limite":    limite,
        "total":     total,
        "restantes": max(0, limite - total),
        "atingido":  total >= limite,
    })


@app.post("/api/adicionar")
async def adicionar_url(request: Request, body: AdicionarRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar antes de adicionar"}, status_code=409)

    # Verifica limite de teste para usuário comum
    token = request.cookies.get("mercadoi_session", "")
    nivel = _get_session_nivel(token) or ("admin" if not _SENHA and not _ADMIN_SENHA else "")
    cfg = _load_config()
    limite = int(cfg.get("limite_links_teste", 0) or 0)
    if limite and nivel != "admin":
        try:
            _db_tmp = _db_manager()
            with _db_tmp._conn() as conn:
                total_atual = conn.execute("SELECT COUNT(*) FROM imoveis").fetchone()[0]
            if total_atual >= limite:
                return JSONResponse({
                    "ok": False,
                    "msg": f"Limite de {limite} links atingido para este período de teste. Entre em contato para ampliar.",
                    "limite_atingido": True,
                }, status_code=429)
        except Exception:
            pass

    entradas_raw = _extrair_entradas_adicionar(body.urls)
    if not entradas_raw:
        return JSONResponse({"ok": False, "msg": "Nenhuma URL informada"}, status_code=400)

    try:
        sheet = _db_manager()
        _, rows = sheet._todas_as_linhas()
        existentes = {}
        originais_existentes = {}
        for r in rows:
            url_existente = r.get("url_instagram", "").strip()
            url_chave = _normalizar_url_entrada(url_existente)
            if url_chave and url_chave not in existentes:
                existentes[url_chave] = r.get("status", "")
                originais_existentes[url_chave] = url_existente
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"Erro ao conectar à planilha: {e}"}, status_code=500)

    results = []
    adicionadas_agora: set[str] = set()

    _STATUS_PUBLICADO = {"rascunho_salvo", "rascunho_salvo_sem_midia_video", "publicado"}

    def _pode_reativar(status: str) -> bool:
        return "erro" in status.lower() or status == "processando"

    def _pode_inserir_novamente(status: str) -> bool:
        return status not in {"pendente", "processando"}

    for entrada in entradas_raw:
        url = entrada["url"]
        whatsapp_contato = entrada.get("whatsapp_contato", "")
        if not _url_suportada(url):
            results.append({"url": url, "status": "invalida", "msg": "URL não é um post do Instagram, anúncio do OLX ou empreendimento do Órulo"})
            continue
        if url in adicionadas_agora:
            results.append({"url": url, "status": "duplicada", "msg": "Duplicada neste envio"})
            continue
        if url in existentes:
            status_existente = existentes[url]
            if whatsapp_contato:
                try:
                    sheet.atualizar_whatsapp_por_url(originais_existentes.get(url, url), whatsapp_contato)
                except Exception:
                    pass
            if body.forcar and _pode_inserir_novamente(status_existente):
                try:
                    linha = sheet.adicionar_pendente(url, whatsapp_contato=whatsapp_contato)
                    adicionadas_agora.add(url)
                    msg_reativ = "Duplicada inserida novamente para novo processamento"
                    results.append({"url": url, "status": "adicionada", "linha": linha, "msg": msg_reativ})
                except Exception as e:
                    results.append({"url": url, "status": "erro", "msg": str(e)})
            elif _pode_reativar(status_existente):
                try:
                    sheet.resetar_url(originais_existentes.get(url, url))
                    if whatsapp_contato:
                        sheet.atualizar_whatsapp_por_url(originais_existentes.get(url, url), whatsapp_contato)
                    adicionadas_agora.add(url)
                    results.append({"url": url, "status": "adicionada", "msg": "Reativada para reprocessamento"})
                except Exception as e:
                    results.append({"url": url, "status": "erro", "msg": str(e)})
            else:
                if status_existente in _STATUS_PUBLICADO:
                    msg_dup = "Já publicado com sucesso"
                    status_dup = "publicado"
                elif status_existente == "pendente":
                    msg_dup = "Já está na fila aguardando processamento"
                    status_dup = "pendente"
                elif status_existente == "processando":
                    msg_dup = "Ja esta em processamento agora"
                    status_dup = "processando"
                else:
                    msg_dup = "Já existe na fila"
                    status_dup = status_existente
                results.append({
                    "url": url,
                    "status": "duplicada",
                    "msg": msg_dup,
                    "status_existente": status_dup,
                    "forcavel": _pode_inserir_novamente(status_existente),
                })
            continue
        try:
            linha = sheet.adicionar_pendente(url, whatsapp_contato=whatsapp_contato)
            adicionadas_agora.add(url)
            results.append({"url": url, "status": "adicionada", "linha": linha, "msg": f"Linha {linha}"})
        except Exception as e:
            results.append({"url": url, "status": "erro", "msg": str(e)})

    duplicadas_forcaveis = [r for r in results if r["status"] == "duplicada" and r.get("forcavel")]

    return JSONResponse({
        "ok": True,
        "results": results,
        "adicionadas": sum(1 for r in results if r["status"] == "adicionada"),
        "duplicadas":  sum(1 for r in results if r["status"] == "duplicada"),
        "invalidas":   sum(1 for r in results if r["status"] == "invalida"),
        "erros":       sum(1 for r in results if r["status"] == "erro"),
        "tem_forcaveis": len(duplicadas_forcaveis) > 0,
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
            # WordPress / API
            "usar_wordpress_api":       cfg.get("usar_wordpress_api", False),
            "wordpress_api_url":        cfg.get("wordpress_api_url", ""),
            "wordpress_wp_user":          cfg.get("wordpress_wp_user", ""),
            "wordpress_app_password":    cfg.get("wordpress_app_password", ""),
            "wordpress_api_key":         cfg.get("wordpress_api_key", ""),
            "wordpress_xmlrpc_url":      cfg.get("wordpress_xmlrpc_url", ""),
            "wordpress_xmlrpc_user":     cfg.get("wordpress_xmlrpc_user", ""),
            "wordpress_xmlrpc_password": cfg.get("wordpress_xmlrpc_password", ""),
            # Apify
            "usar_apify":               cfg.get("usar_apify", False),
            "apify_api_token":          cfg.get("apify_api_token", ""),
            "apify_actor_id":           cfg.get("apify_actor_id", ""),
            # Workers
            "max_workers":              cfg.get("max_workers", 1),
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
            "cache_extracao_ttl_horas": cfg.get("cache_extracao_ttl_horas", 12),
            "limpeza_auto_dias":        cfg.get("limpeza_auto_dias", 15),
            "orulo_agent_id":           cfg.get("orulo_agent_id", ""),
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


@app.get("/api/gerar-licenca")
async def gerar_licenca(data: str = ""):
    """Gera os valores assinados de licença para enviar ao cliente (sem salvar localmente)."""
    try:
        expira = data.strip()
        if expira:
            date.fromisoformat(expira)
        sig = _assinar_licenca(expira) if expira else ""
        return JSONResponse({
            "ok": True,
            "licenca_expira": expira,
            "licenca_assinatura": sig,
        })
    except ValueError:
        return JSONResponse({"ok": False, "msg": "Data inválida."}, status_code=400)


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
    cache_extracao_ttl_horas: int | None = None
    limpeza_auto_dias:        int | None = None
    orulo_agent_id:        str | None = None


@app.post("/api/config/avancada")
async def salvar_config_avancada(body: ConfigAvancadaRequest):
    try:
        cfg = _load_config()
        campos = body.model_dump(exclude_none=True)
        # Sanitizar strings
        for k, v in campos.items():
            if isinstance(v, str):
                campos[k] = v.strip()
        if "cache_extracao_ttl_horas" in campos:
            campos["cache_extracao_ttl_horas"] = max(0, min(int(campos["cache_extracao_ttl_horas"]), 168))
        if "limpeza_auto_dias" in campos:
            campos["limpeza_auto_dias"] = max(0, min(int(campos["limpeza_auto_dias"]), 365))
        cfg.update(campos)
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class WordPressConfigRequest(BaseModel):
    usar_wordpress_api:       bool = False
    wordpress_api_url:        str  = ""
    wordpress_wp_user:        str  = ""
    wordpress_app_password:   str  = ""
    wordpress_api_key:        str  = ""
    wordpress_xmlrpc_url:     str  = ""
    wordpress_xmlrpc_user:    str  = ""
    wordpress_xmlrpc_password:str  = ""
    usar_apify:               bool = False
    apify_api_token:          str  = ""
    apify_actor_id:           str  = ""
    max_workers:              int  = 1


@app.post("/api/config/wordpress")
async def salvar_config_wordpress(body: WordPressConfigRequest):
    try:
        cfg = _load_config()
        campos = body.model_dump()
        for k, v in campos.items():
            if isinstance(v, str):
                campos[k] = v.strip()
        if not (1 <= campos["max_workers"] <= 10):
            return JSONResponse({"ok": False, "msg": "max_workers deve ser entre 1 e 10"}, status_code=400)
        cfg.update(campos)
        (BASE_DIR / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class ConectarWordPressRequest(BaseModel):
    site_url:    str
    wp_user:     str
    wp_password: str


@app.post("/api/conectar-wordpress")
async def conectar_wordpress(body: ConectarWordPressRequest):
    """Acessível ao usuário comum — permite que o cliente conecte o próprio site."""
    site_url    = body.site_url.strip().rstrip("/")
    wp_user     = body.wp_user.strip()
    wp_password = body.wp_password.strip()

    if not site_url or not wp_user or not wp_password:
        return JSONResponse({"ok": False, "msg": "Preencha todos os campos"}, status_code=400)

    if not site_url.startswith("http"):
        site_url = "https://" + site_url

    # Verifica se xmlrpc.php responde antes de salvar
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{site_url}/xmlrpc.php")
            if r.status_code not in (200, 405):
                return JSONResponse({
                    "ok": False,
                    "msg": f"Não consegui acessar {site_url}/xmlrpc.php (HTTP {r.status_code}). Verifique a URL do site."
                }, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"Não consegui conectar ao site: {e}"}, status_code=400)

    cfg = _load_config()
    cfg["usar_wordpress_api"]       = True
    cfg["wordpress_xmlrpc_url"]     = site_url
    cfg["wordpress_xmlrpc_user"]    = wp_user
    cfg["wordpress_xmlrpc_password"]= wp_password
    # Limpa modos alternativos para evitar conflito
    cfg["wordpress_api_key"]        = ""
    cfg["wordpress_app_password"]   = ""
    (BASE_DIR / "config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return JSONResponse({"ok": True, "msg": "WordPress conectado com sucesso!"})


@app.get("/api/wordpress-status")
async def wordpress_status():
    """Retorna se o WordPress está conectado — acessível a todos."""
    cfg = _load_config()
    conectado = (
        bool(cfg.get("usar_wordpress_api"))
        and (
            bool(cfg.get("wordpress_xmlrpc_url") and cfg.get("wordpress_xmlrpc_user") and cfg.get("wordpress_xmlrpc_password"))
            or bool(cfg.get("wordpress_api_key"))
            or bool(cfg.get("wordpress_app_password"))
        )
    )
    return JSONResponse({"conectado": conectado})


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


@app.post("/api/exportar-sessao-mercadoi")
async def exportar_sessao_mercadoi():
    """Conecta ao Chrome Windows (porta 9222) e salva os cookies do Mercadoi."""
    if sys.platform != "win32":
        return JSONResponse({"ok": False, "msg": "Export só disponível no Windows (Chrome local)."}, status_code=400)
    try:
        from playwright.async_api import async_playwright
        cfg = _load_config()
        site = cfg.get("mercadoi_url", "https://mercadoi.com.br").rstrip("/")
        from urllib.parse import urlparse
        domain = urlparse(site).netloc

        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            except Exception:
                return JSONResponse({"ok": False, "msg": "Chrome não encontrado na porta 9222. Abra o Chrome do Mercadoi antes."}, status_code=400)
            if not browser.contexts:
                return JSONResponse({"ok": False, "msg": "Chrome conectado mas sem contexto/aba aberta."}, status_code=500)
            context = browser.contexts[0]
            todos = await context.cookies()

        filtrados = [c for c in todos if domain in c.get("domain", "")]
        if not filtrados:
            filtrados = todos  # salva tudo se nenhum for do domínio
        _SKIP = {"wordpress_test_cookie"}
        filtrados = [c for c in filtrados if c.get("name") not in _SKIP]

        cookies_file = _DATA_DIR / "mercadoi_session.json"
        cookies_file.write_text(
            json.dumps(filtrados, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({
            "ok": True,
            "msg": f"Sessão exportada: {len(filtrados)} cookies salvos.",
            "arquivo": str(cookies_file),
            "cookies": filtrados,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"Erro: {e}"}, status_code=500)


@app.get("/api/download-sessao-mercadoi")
async def download_sessao_mercadoi():
    """Baixa o arquivo mercadoi_session.json para transferir à VPS."""
    f = _DATA_DIR / "mercadoi_session.json"
    if not f.exists():
        raise HTTPException(404, "Arquivo não encontrado. Execute o export primeiro.")
    return FileResponse(str(f), filename="mercadoi_session.json", media_type="application/json")


class ImportarSessaoRequest(BaseModel):
    cookies: list


@app.post("/api/importar-sessao-mercadoi")
async def importar_sessao_mercadoi(body: ImportarSessaoRequest):
    """Recebe cookies JSON e salva como mercadoi_session.json (uso no VPS)."""
    try:
        if not body.cookies:
            return JSONResponse({"ok": False, "msg": "Lista de cookies vazia."}, status_code=400)
        f = _DATA_DIR / "mercadoi_session.json"
        f.write_text(json.dumps(body.cookies, indent=2, ensure_ascii=False), encoding="utf-8")
        return JSONResponse({"ok": True, "msg": f"{len(body.cookies)} cookies importados com sucesso."})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/api/sessao-mercadoi-status")
async def sessao_mercadoi_status():
    """Verifica se existe sessão salva e quantos cookies tem."""
    f = _DATA_DIR / "mercadoi_session.json"
    if not f.exists():
        return JSONResponse({"existe": False, "cookies": 0})
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return JSONResponse({"existe": True, "cookies": len(data)})
    except Exception:
        return JSONResponse({"existe": False, "cookies": 0})


class RemoverRequest(BaseModel):
    id: int


def _contar_arquivos_antigos(pastas: list[Path], dias: int) -> tuple[int, int]:
    limite = time.time() - (dias * 86400)
    count = 0
    bytes_total = 0
    vistos: set[str] = set()
    for pasta in pastas:
        try:
            pasta = pasta.resolve()
        except Exception:
            continue
        if str(pasta) in vistos or not pasta.exists():
            continue
        vistos.add(str(pasta))
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho = Path(raiz) / nome
                try:
                    st = caminho.stat()
                    if st.st_mtime < limite:
                        count += 1
                        bytes_total += st.st_size
                except Exception:
                    pass
    return count, bytes_total


def _remover_arquivos_antigos(pastas: list[Path], dias: int) -> tuple[int, int]:
    limite = time.time() - (dias * 86400)
    count = 0
    bytes_total = 0
    vistos: set[str] = set()
    for pasta in pastas:
        try:
            pasta = pasta.resolve()
        except Exception:
            continue
        if str(pasta) in vistos or not pasta.exists():
            continue
        vistos.add(str(pasta))
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho = Path(raiz) / nome
                try:
                    st = caminho.stat()
                    if st.st_mtime < limite:
                        bytes_total += st.st_size
                        caminho.unlink()
                        count += 1
                except Exception:
                    pass
    return count, bytes_total


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


@app.post("/api/priorizar")
async def priorizar_item(body: RemoverRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    try:
        db = _db_manager()
        ok = db.priorizar_item(body.id)
        if ok:
            return JSONResponse({"ok": True, "msg": "Item movido para o topo da fila"})
        return JSONResponse({"ok": False, "msg": "Item não encontrado ou já em processamento"}, status_code=404)
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


@app.post("/api/limpar-erros")
async def limpar_erros():
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    try:
        db = _db_manager()
        count = db.limpar_erros()
        return JSONResponse({"ok": True, "msg": f"{count} erro(s) removido(s)"})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


class LimpezaRequest(BaseModel):
    dias: int = 15


@app.post("/api/limpar-arquivos-antigos")
async def limpar_arquivos_antigos(body: LimpezaRequest):
    if _bot_rodando:
        return JSONResponse({"ok": False, "msg": "Aguarde o bot terminar"}, status_code=409)
    dias = max(1, min(int(body.dias or 15), 365))
    try:
        cfg = _load_config()
        pastas = [
            Path(cfg.get("downloads_path") or (_DATA_DIR / "downloads")),
            _DATA_DIR / "downloads",
            LOGS_DIR,
            LOGS_DIR / "screenshots",
        ]
        arquivos, bytes_total = _remover_arquivos_antigos(pastas, dias)
        cache = _db_manager().limpar_cache_expirado(max(1, min(dias, 3)))
        return JSONResponse({
            "ok": True,
            "msg": f"{arquivos} arquivo(s) e {cache} cache(s) antigo(s) removido(s)",
            "arquivos": arquivos,
            "cache": cache,
            "bytes": bytes_total,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


@app.get("/api/diagnostico")
async def diagnostico():
    try:
        cfg = _load_config()
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"config.json inválido: {e}"}, status_code=500)

    checks = []

    def add(nome: str, ok: bool, detalhe: str = ""):
        checks.append({"nome": nome, "ok": bool(ok), "detalhe": detalhe})

    db_ok = False
    cache_count = 0
    fila_count = 0
    tempos = {"captura": 0, "midia": 0, "envio": 0, "gargalo": ""}
    try:
        db = _db_manager()
        with db._conn() as conn:
            fila_count = conn.execute("SELECT COUNT(*) AS c FROM imoveis WHERE status='pendente'").fetchone()["c"]
            cache_count = conn.execute("SELECT COUNT(*) AS c FROM extracao_cache").fetchone()["c"]
            row_t = conn.execute(
                "SELECT AVG(captura_seg) AS captura, AVG(midia_seg) AS midia, AVG(publicacao_seg) AS envio "
                "FROM (SELECT captura_seg, midia_seg, publicacao_seg FROM imoveis "
                "WHERE tempo_seg > 0 AND status IN ('rascunho_salvo','rascunho_salvo_sem_midia_video','publicado') "
                "ORDER BY id DESC LIMIT 20)"
            ).fetchone()
            if row_t:
                tempos = {
                    "captura": round(row_t["captura"] or 0),
                    "midia": round(row_t["midia"] or 0),
                    "envio": round(row_t["envio"] or 0),
                    "gargalo": "",
                }
                pares = {"captura": tempos["captura"], "mídia": tempos["midia"], "envio": tempos["envio"]}
                tempos["gargalo"] = max(pares, key=pares.get) if any(pares.values()) else ""
        db_ok = True
    except Exception as e:
        add("Banco SQLite", False, str(e))
    if db_ok:
        add("Banco SQLite", True, f"{fila_count} pendente(s), {cache_count} cache(s)")

    usar_wp = bool(cfg.get("usar_wordpress_api"))
    xmlrpc_ok = bool(cfg.get("wordpress_xmlrpc_url") and cfg.get("wordpress_xmlrpc_user") and cfg.get("wordpress_xmlrpc_password"))
    rest_ok = bool(cfg.get("wordpress_api_url") and (cfg.get("wordpress_api_key") or cfg.get("wordpress_app_password")))
    add("WordPress", usar_wp and (xmlrpc_ok or rest_ok), "XML-RPC configurado" if xmlrpc_ok else ("REST configurado" if rest_ok else "não configurado"))

    add("DeepSeek API", bool(cfg.get("deepseek_api_key")), "chave configurada" if cfg.get("deepseek_api_key") else "sem chave")
    add("Apify", bool(cfg.get("usar_apify") and cfg.get("apify_api_token")), "ativo" if cfg.get("usar_apify") else "desativado")
    add("OLX Worker", bool(cfg.get("olx_worker_url")), cfg.get("olx_worker_url", "")[:70])
    add("Órulo", bool(cfg.get("orulo_email") and cfg.get("orulo_senha")), "login configurado" if cfg.get("orulo_email") else "sem login")

    sessao = _DATA_DIR / "mercadoi_session.json"
    cookies = 0
    if sessao.exists():
        try:
            cookies = len(json.loads(sessao.read_text(encoding="utf-8")))
        except Exception:
            cookies = 0
    add("Sessão Mercadoi", cookies > 0, f"{cookies} cookie(s)" if cookies else "sem arquivo de sessão")

    downloads = Path(cfg.get("downloads_path") or (_DATA_DIR / "downloads"))
    add("Downloads", downloads.exists(), str(downloads))

    try:
        usage = shutil.disk_usage(str(_DATA_DIR))
        disco = {
            "total_gb": round(usage.total / (1024**3), 1),
            "livre_gb": round(usage.free / (1024**3), 1),
            "uso_pct": round((usage.used / usage.total) * 100),
        }
    except Exception:
        disco = {"total_gb": 0, "livre_gb": 0, "uso_pct": 0}

    dias_limpeza = int(cfg.get("limpeza_auto_dias", 15) or 15)
    antigos, bytes_antigos = _contar_arquivos_antigos(
        [downloads, _DATA_DIR / "downloads", LOGS_DIR, LOGS_DIR / "screenshots"],
        max(1, dias_limpeza),
    )

    return JSONResponse({
        "ok": True,
        "checks": checks,
        "disco": disco,
        "limpeza": {
            "dias": dias_limpeza,
            "arquivos_antigos": antigos,
            "bytes_antigos": bytes_antigos,
        },
        "cache_ttl_horas": int(cfg.get("cache_extracao_ttl_horas", 12) or 0),
        "tempos": tempos,
    })


@app.get("/api/fila")
async def listar_fila():
    """Retorna todos os itens pendentes e com erro para exibição na fila."""
    try:
        db = _db_manager()
        _EM_ANDAMENTO = {"pendente", "processando", "capturando", "baixando_midia", "enviando_wp"}
        with db._conn() as conn:
            rows = [
                db._to_dict(r)
                for r in conn.execute(
                    "SELECT * FROM imoveis "
                    "WHERE status IN ('pendente','processando','capturando','baixando_midia','enviando_wp') "
                    "OR status LIKE 'erro%' "
                    "ORDER BY prioridade DESC, id DESC"
                ).fetchall()
            ]
        # Deduplica pendentes por URL (igual ao listar_pendentes do DB),
        # para que o count da fila bata com o card "NA FILA" do dashboard.
        _urls_pendentes: set[str] = set()
        fila = []
        for r in rows:
            status = r.get("status", "")
            url = r.get("url_instagram", "")
            if status == "pendente":
                if url in _urls_pendentes:
                    continue
                _urls_pendentes.add(url)
            if status in _EM_ANDAMENTO or "erro" in status.lower():
                fila.append({
                    "id":            r["_row_index"],
                    "url":           url,
                    "status":        status,
                    "mensagem_erro": r.get("mensagem_erro", ""),
                    "tentativas":    r.get("tentativas", 0),
                    "criado_em":     r.get("criado_em", ""),
                    "atualizado_em": r.get("atualizado_em", ""),
                    "mercadoi_url":  r.get("mercadoi_url", ""),
                    "origem":        r.get("origem", "") or _fonte(url),
                    "prioridade":    int(r.get("prioridade") or 0),
                })
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
                        def _ver(v):
                            try: return tuple(int(x) for x in v.split("."))
                            except: return (0,)
                        has_update = _ver(versao_remota) > _ver(VERSION)

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
    import httpx as _httpx
    import zipfile as _zf
    import tempfile as _tf
    import shutil as _sh
    try:
        cfg = _load_config()
        zip_url = cfg.get("update_zip_url", "").strip()
        if not zip_url:
            return JSONResponse(
                {"ok": False, "msg": "update_zip_url não configurado em Config Avançada."},
                status_code=400,
            )

        async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(zip_url)
            if r.status_code != 200:
                return JSONResponse(
                    {"ok": False, "msg": f"Falha ao baixar (HTTP {r.status_code})."},
                    status_code=500,
                )
        zip_bytes = r.content

        def _aplicar() -> int:
            with _tf.TemporaryDirectory() as tmp:
                zp = os.path.join(tmp, "u.zip")
                open(zp, "wb").write(zip_bytes)
                # Extrai seletivamente — pula cloudflared.exe e outros preservados
                with _zf.ZipFile(zp) as z:
                    for item in z.infolist():
                        nome = os.path.basename(item.filename)
                        if nome and nome in _PRESERVAR_ARQUIVOS:
                            continue
                        z.extract(item, tmp)
                # ZIP nosso é flat — usa tmp diretamente como raiz
                n = 0
                for root, dirs, files in os.walk(tmp):
                    dirs[:] = [d for d in dirs if d not in _PRESERVAR_PASTAS]
                    rel = os.path.relpath(root, tmp)
                    for fname in files:
                        if fname in _PRESERVAR_ARQUIVOS or fname == "u.zip":
                            continue
                        dst = BASE_DIR if rel == "." else BASE_DIR / rel
                        dst.mkdir(parents=True, exist_ok=True)
                        _sh.copy2(os.path.join(root, fname), dst / fname)
                        n += 1
            flag = BASE_DIR / ".installed"
            if flag.exists():
                flag.unlink()
            return n

        copiados = await asyncio.get_event_loop().run_in_executor(None, _aplicar)

        # Agenda reinício via cmd externo — aguarda 4s (porta libera) depois sobe novo processo
        import threading as _threading
        def _restart():
            import time as _t, subprocess as _sp
            log_path = str(BASE_DIR / "logs" / "painel.log")
            cmd = (
                f'cmd /c timeout /t 4 /nobreak > nul'
                f' && "{sys.executable}" "{BASE_DIR / "panel.py"}"'
                f' >> "{log_path}" 2>&1'
            )
            _sp.Popen(
                cmd, shell=True,
                creationflags=_sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP,
                stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            _t.sleep(0.5)  # garante que o cmd externo iniciou antes de sair
            os._exit(0)

        _threading.Thread(target=_restart, daemon=False).start()

        return JSONResponse({
            "ok":        True,
            "reiniciando": True,
            "msg":       f"Atualização aplicada ({copiados} arquivos). Reiniciando o painel...",
        })

    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Bot Mercadoi — Painel")
    print("  Acesse: http://localhost:8000")
    print("=" * 50)
    host = "127.0.0.1" if sys.platform == "win32" else "0.0.0.0"
    uvicorn.run(app, host=host, port=8000, log_level="warning")
