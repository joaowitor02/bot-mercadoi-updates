"""
Servidor simples de licencas para o Bot Mercadoi.

Hospede este app em uma VPS/Render/Railway/Fly.io e mantenha o arquivo
licenses.json fora do pacote entregue ao cliente.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
LICENSES_PATH = Path(os.environ.get("LICENSES_PATH", BASE_DIR / "licenses.json"))
ADMIN_TOKEN = os.environ.get("LICENSE_ADMIN_TOKEN", "")
DEFAULT_CACHE_SECONDS = int(os.environ.get("LICENSE_CACHE_SECONDS", "86400"))

app = FastAPI(title="Bot Mercadoi License Server")


class ValidateRequest(BaseModel):
    license_key: str
    machine_id: str
    app_version: str = ""


class LicenseCreateRequest(BaseModel):
    cliente: str
    expira_em: str
    limite_maquinas: int = 1
    ativa: bool = True


def _load_licenses() -> dict[str, Any]:
    if not LICENSES_PATH.exists():
        return {}
    return json.loads(LICENSES_PATH.read_text(encoding="utf-8"))


def _save_licenses(data: dict[str, Any]) -> None:
    LICENSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _expired(expira_em: str) -> bool:
    if not expira_em:
        return False
    try:
        return date.fromisoformat(expira_em) < date.today()
    except Exception:
        return True


def _cache_until(expira_em: str) -> str:
    until = datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_CACHE_SECONDS)
    if expira_em:
        try:
            expires = datetime.combine(date.fromisoformat(expira_em), datetime.max.time(), tzinfo=timezone.utc)
            until = min(until, expires)
        except Exception:
            pass
    return until.isoformat()


def _require_admin(authorization: str | None) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="LICENSE_ADMIN_TOKEN nao configurado")
    token = (authorization or "").replace("Bearer ", "").strip()
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Token admin invalido")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/validate")
async def validate_license(body: ValidateRequest):
    key = body.license_key.strip()
    machine_id = body.machine_id.strip()
    if not key or not machine_id:
        return {"active": False, "machine_allowed": False, "message": "Licenca ou maquina ausente"}

    data = _load_licenses()
    lic = data.get(key)
    if not lic:
        return {"active": False, "machine_allowed": False, "message": "Licenca nao encontrada"}

    if not lic.get("ativa", True):
        return {"active": False, "machine_allowed": False, "message": "Licenca inativa"}

    expira_em = str(lic.get("expira_em", "")).strip()
    if _expired(expira_em):
        return {
            "active": False,
            "machine_allowed": False,
            "cliente": lic.get("cliente", ""),
            "expires_at": expira_em,
            "message": "Licenca expirada",
        }

    maquinas = list(lic.get("maquinas") or [])
    limite = int(lic.get("limite_maquinas") or 1)

    if machine_id not in maquinas:
        if len(maquinas) >= limite:
            return {
                "active": False,
                "machine_allowed": False,
                "cliente": lic.get("cliente", ""),
                "expires_at": expira_em,
                "message": "Limite de maquinas atingido",
            }
        maquinas.append(machine_id)
        lic["maquinas"] = maquinas
        lic["ativada_em"] = lic.get("ativada_em") or datetime.now(timezone.utc).isoformat()
        lic["ultimo_app_version"] = body.app_version
        lic["ultimo_check"] = datetime.now(timezone.utc).isoformat()
        data[key] = lic
        _save_licenses(data)
    else:
        lic["ultimo_app_version"] = body.app_version
        lic["ultimo_check"] = datetime.now(timezone.utc).isoformat()
        data[key] = lic
        _save_licenses(data)

    return {
        "active": True,
        "machine_allowed": True,
        "cliente": lic.get("cliente", ""),
        "expires_at": expira_em,
        "cache_seconds": DEFAULT_CACHE_SECONDS,
        "cache_until": _cache_until(expira_em),
        "message": "Licenca valida",
    }


@app.post("/admin/licenses")
async def create_license(body: LicenseCreateRequest, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    date.fromisoformat(body.expira_em)
    data = _load_licenses()
    key = "BMI-" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:20].upper()
    data[key] = {
        "cliente": body.cliente.strip(),
        "ativa": body.ativa,
        "expira_em": body.expira_em,
        "limite_maquinas": max(1, body.limite_maquinas),
        "maquinas": [],
        "criada_em": datetime.now(timezone.utc).isoformat(),
    }
    _save_licenses(data)
    return {"ok": True, "license_key": key, "license": data[key]}
