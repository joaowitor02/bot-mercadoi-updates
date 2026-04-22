"""
Validacao de licenca do Bot Mercadoi.

A protecao forte depende de uma validacao online em servidor controlado pelo dono
do produto. Esta camada bloqueia o processamento quando o licenciamento estiver
habilitado no config.json e a chave/maquina nao forem aceitas pelo servidor.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx


@dataclass
class LicenseStatus:
    ok: bool
    message: str
    machine_id: str
    cliente: str = ""
    expires_at: str = ""
    origem: str = "disabled"


def _run_quiet(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()
    except Exception:
        return ""


def _windows_uuid() -> str:
    if platform.system().lower() != "windows":
        return ""
    output = _run_quiet(["wmic", "csproduct", "get", "uuid"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) >= 2 and "uuid" not in lines[-1].lower():
        return lines[-1]
    return ""


def _windows_disk_serial() -> str:
    if platform.system().lower() != "windows":
        return ""
    output = _run_quiet(["wmic", "diskdrive", "get", "serialnumber"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    serials = [line for line in lines if "serial" not in line.lower()]
    return "|".join(serials[:3])


def machine_id() -> str:
    parts = [
        platform.system(),
        platform.machine(),
        platform.node(),
        socket.gethostname(),
        os.environ.get("USERNAME", ""),
        os.environ.get("USERDOMAIN", ""),
        _windows_uuid(),
        _windows_disk_serial(),
    ]
    raw = "|".join(str(part).strip().lower() for part in parts if str(part).strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _cache_path(config: dict) -> Path:
    explicit = str(config.get("licenca_cache_path", "")).strip()
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parent.parent / "license_cache.json"


def _read_cache(config: dict, expected_key: str, expected_machine: str) -> LicenseStatus | None:
    path = _cache_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if data.get("license_key") != expected_key or data.get("machine_id") != expected_machine:
        return None

    cache_until = _parse_dt(str(data.get("cache_until", "")))
    if not cache_until or cache_until < _utc_now():
        return None

    return LicenseStatus(
        ok=True,
        message="Licenca validada pelo cache local",
        machine_id=expected_machine,
        cliente=str(data.get("cliente", "")),
        expires_at=str(data.get("expires_at", "")),
        origem="cache",
    )


def _write_cache(config: dict, payload: dict, license_key: str, current_machine: str) -> None:
    cache_until = str(payload.get("cache_until", "")).strip()
    if not cache_until:
        cache_seconds = int(payload.get("cache_seconds") or int(config.get("licenca_cache_horas", 24)) * 3600)
        cache_until = datetime.fromtimestamp(_utc_now().timestamp() + cache_seconds, tz=timezone.utc).isoformat()

    data = {
        "license_key": license_key,
        "machine_id": current_machine,
        "cliente": payload.get("cliente", ""),
        "expires_at": payload.get("expires_at", ""),
        "cache_until": cache_until,
        "checked_at": _utc_now().isoformat(),
    }
    path = _cache_path(config)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def validar_licenca(config: dict, app_version: str = "") -> LicenseStatus:
    if not bool(config.get("licenciamento_habilitado", False)):
        return LicenseStatus(True, "Licenciamento desabilitado", machine_id(), origem="disabled")

    license_key = str(config.get("licenca_chave", "")).strip()
    server_url = str(config.get("licenca_servidor_url", "")).strip().rstrip("/")
    current_machine = machine_id()

    if not license_key:
        return LicenseStatus(False, "Licenca nao configurada: informe licenca_chave", current_machine)
    if not server_url:
        return LicenseStatus(False, "Servidor de licenca nao configurado", current_machine)

    payload = {
        "license_key": license_key,
        "machine_id": current_machine,
        "app_version": app_version,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{server_url}/validate", json=payload)
        if resp.status_code != 200:
            cached = _read_cache(config, license_key, current_machine)
            if cached:
                return cached
            return LicenseStatus(False, f"Servidor recusou a licenca: HTTP {resp.status_code}", current_machine)

        data = resp.json()
        if not data.get("active"):
            return LicenseStatus(False, str(data.get("message") or "Licenca inativa"), current_machine)
        if not data.get("machine_allowed", True):
            return LicenseStatus(False, str(data.get("message") or "Maquina nao autorizada"), current_machine)

        _write_cache(config, data, license_key, current_machine)
        return LicenseStatus(
            ok=True,
            message=str(data.get("message") or "Licenca valida"),
            machine_id=current_machine,
            cliente=str(data.get("cliente", "")),
            expires_at=str(data.get("expires_at", "")),
            origem="online",
        )
    except Exception as exc:
        cached = _read_cache(config, license_key, current_machine)
        if cached:
            return cached
        return LicenseStatus(False, f"Nao foi possivel validar a licenca: {exc}", current_machine)
