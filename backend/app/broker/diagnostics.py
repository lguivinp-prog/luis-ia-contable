"""Fase 5, Bloque 5.2, Domain G -- diagnostico operativo SEGURO del broker SOL.

    python -m app.broker.diagnostics

Imprime JSON con SOLO hechos no secretos: si el broker esta habilitado, si existe material de CA (nombres de archivo y
huella SHA-256 del certificado PUBLICO -- nunca contenido de claves), y el estado de los agentes emparejados (id,
estado, fechas). Nunca imprime claves privadas, tokens de job, connection_id, secretos de bovedas ni credenciales SOL.

La disponibilidad "en vivo" (conexion mTLS actual) solo la conoce el proceso backend; aqui se informa la ultima senal
de vida registrada (`last_seen_at`) frente al umbral configurado."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.broker import pki
from app.core.config import get_settings


def _ca_report(ca_dir: Path) -> dict[str, object]:
    cert_path, key_path = ca_dir / "ca.cert.pem", ca_dir / "ca.key.pem"
    report: dict[str, object] = {
        "ca_dir_exists": ca_dir.is_dir(),
        "ca_certificate_file_present": cert_path.is_file(),
        "ca_key_file_present": key_path.is_file(),
        "ca_loadable": False,
    }
    try:
        ca = pki.load_ca(ca_dir=ca_dir)
    except (pki.CaMaterialMissingError, pki.CaMaterialInvalidError):
        return report
    report["ca_loadable"] = True
    report["ca_certificate_sha256"] = pki.fingerprint_sha256_hex(ca.certificate)
    report["ca_not_valid_after"] = ca.certificate.not_valid_after_utc.isoformat()
    return report


def collect() -> dict[str, object]:
    settings = get_settings()
    out: dict[str, object] = {
        "broker_enabled_setting": settings.sol_broker_enabled,
        "broker_host": settings.sol_broker_host,
        "broker_pairing_port": settings.sol_broker_pairing_port,
        "broker_operational_port": settings.sol_broker_operational_port,
        "online_threshold_seconds": settings.sol_agent_online_threshold_seconds,
        **_ca_report(Path(settings.sol_broker_ca_dir)),
    }
    try:
        from app.db.session import SessionLocal
        from app.models.sol_agent_access import SolAgent

        now = datetime.now(UTC)
        db = SessionLocal()
        try:
            agents = []
            for agent in db.execute(select(SolAgent)).scalars().all():
                age = (now - agent.last_seen_at).total_seconds() if agent.last_seen_at else None
                agents.append(
                    {
                        "agent_id": str(agent.id),
                        "tenant_id": str(agent.tenant_id),
                        "status": agent.status,
                        "paired_at": agent.paired_at.isoformat() if agent.paired_at else None,
                        "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
                        "seconds_since_last_seen": round(age, 1) if age is not None else None,
                        "recently_seen": age is not None and age <= settings.sol_agent_online_threshold_seconds,
                    }
                )
        finally:
            db.close()
        out["database_reachable"] = True
        out["agents"] = agents
    except Exception as exc:  # noqa: BLE001 -- diagnostico: se informa el tipo, nunca el mensaje (puede traer DSN)
        out["database_reachable"] = False
        out["database_error_type"] = type(exc).__name__
    return out


def main() -> int:
    print(json.dumps(collect(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
