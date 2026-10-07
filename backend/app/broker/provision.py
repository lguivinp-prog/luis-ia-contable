"""Fase 5, Bloque 5.2, Domain G -- aprovisionamiento EXPLICITO de la CA del broker SOL.

    python -m app.broker.provision --ca-dir <DIR> [--export-public <DIR_PUBLICO>]

Es un paso operativo intencional que se ejecuta ANTES de activar `SOL_BROKER_ENABLED=true`. El arranque del backend
sigue sin autogenerar nunca una CA (`product_startup.start_production_broker` solo usa `pki.load_ca`).

Garantias:
- NO sobrescribe: si `--ca-dir` ya contiene material de CA, aborta (la rotacion de la CA es una operacion manual
  deliberada, fuera del alcance de esta herramienta -- invalidaria todos los agentes emparejados).
- La clave privada de la CA nunca se imprime ni se escribe en logs/stdout; solo se informa la huella SHA-256 del
  certificado PUBLICO.
- Se restringe el directorio al usuario actual (ACL de Windows via icacls / modo 0700-0600 en POSIX).
- `--export-public` copia UNICAMENTE `ca.cert.pem` (publico) -- es lo que el Agente necesita para verificar al broker."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from app.broker import pki

_CA_FILES = ("ca.key.pem", "ca.cert.pem")


def restrict_to_current_user(directory: Path) -> bool:
    """Mejor esfuerzo, con resultado informado (nunca se afirma mas de lo que el SO garantiza)."""
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        if not user:
            return False
        completed = subprocess.run(
            ["icacls.exe", str(directory), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        return completed.returncode == 0
    try:
        directory.chmod(0o700)
        for name in _CA_FILES:
            candidate = directory / name
            if candidate.exists():
                candidate.chmod(0o600)
        return True
    except OSError:
        return False


def provision_ca(ca_dir: Path, *, export_public: Path | None = None) -> dict[str, object]:
    existing = [name for name in _CA_FILES if (ca_dir / name).exists()]
    if existing:
        raise SystemExit(
            f"CA_ALREADY_PROVISIONED: '{ca_dir}' ya contiene {existing}. Esta herramienta nunca sobrescribe una CA "
            "existente (rotarla invalida a todos los agentes emparejados; es una operacion manual deliberada)."
        )
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca = pki.generate_ca()
    pki.save_ca(ca, ca_dir=ca_dir)
    acl_ok = restrict_to_current_user(ca_dir)
    result: dict[str, object] = {
        "ca_dir": str(ca_dir),
        "ca_certificate_sha256": pki.fingerprint_sha256_hex(ca.certificate),
        "not_valid_after": ca.certificate.not_valid_after_utc.isoformat(),
        "directory_restricted_to_current_user": acl_ok,
    }
    if export_public is not None:
        export_public.mkdir(parents=True, exist_ok=True)
        (export_public / "ca.cert.pem").write_bytes(ca.certificate.public_bytes(serialization.Encoding.PEM))
        result["public_certificate_exported_to"] = str(export_public)
    return result


def export_public_certificate(ca_dir: Path, export_public: Path) -> Path:
    """Copia SOLO el certificado publico de una CA ya aprovisionada (para la instalacion del Agente)."""
    source = ca_dir / "ca.cert.pem"
    if not source.is_file():
        raise SystemExit(f"CA_CERTIFICATE_MISSING: no existe '{source}'. Aprovisiona la CA primero.")
    export_public.mkdir(parents=True, exist_ok=True)
    target = export_public / "ca.cert.pem"
    shutil.copyfile(source, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.broker.provision", description=__doc__)
    parser.add_argument("--ca-dir", required=True, type=Path, help="Directorio de la CA (fuera del repositorio)")
    parser.add_argument("--export-public", type=Path, default=None, help="Copiar solo ca.cert.pem a este directorio")
    parser.add_argument(
        "--export-only", action="store_true", help="No generar: solo exportar el certificado publico existente"
    )
    args = parser.parse_args(argv)
    if args.export_only:
        if args.export_public is None:
            parser.error("--export-only requiere --export-public")
        target = export_public_certificate(args.ca_dir, args.export_public)
        print(f"PUBLIC_CERTIFICATE_EXPORTED={target}")
        return 0
    result = provision_ca(args.ca_dir, export_public=args.export_public)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
