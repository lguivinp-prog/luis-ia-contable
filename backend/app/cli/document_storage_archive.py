"""CLI declarativo para archivar/restaurar/verificar el volumen de
almacenamiento documental (Bloque 5, Dominio F, ver Seccion 14 del plan --
ADR-0055). Usa unicamente `tarfile`+`hashlib` de la libreria estandar: no
depende de que la imagen `backend` tenga instalado el binario `tar` del
sistema. Se invoca siempre dentro de la propia imagen `backend` (nunca en el
host), tipicamente vía `docker compose run --rm --no-deps backend`:

    python -m app.cli.document_storage_archive archive --output /backups/<id>/volume.tar.gz
    python -m app.cli.document_storage_archive extract --input /backups/<id>/volume.tar.gz --confirm
    python -m app.cli.document_storage_archive verify

Cada subcomando imprime un unico objeto JSON a stdout (una linea) para que
`backup.ps1`/`restore.ps1` lo capturen y compongan el `manifest.json` sin
tener que parsear salida de texto libre."""

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.declaration_document_file import DeclarationDocumentFile

_CHUNK_SIZE = 1024 * 1024
_RETAINED_STATUSES = ("ACTIVE", "SUPERSEDED", "DELETED")


def _sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _clear_directory_contents(path: Path) -> None:
    """Borra todo lo que hay DENTRO de `path`, dejando `path` mismo intacto
    -- necesario porque `path` puede ser el punto de montaje de un volumen
    Docker nombrado, que el SO nunca permite eliminar (a diferencia de un
    directorio comun)."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _cmd_archive(args: argparse.Namespace) -> int:
    """Empaqueta el volumen completo en un `.tar.gz` -- streaming, sin cargar
    ningun blob completo en memoria (`tarfile` lee cada miembro por bloques
    internamente)."""
    root = Path(get_settings().document_storage_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    blob_count = 0
    with tarfile.open(output, "w:gz") as tar:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=str(path.relative_to(root)))
                    blob_count += 1

    print(
        json.dumps(
            {
                "output_path": str(output),
                "volume_sha256": _sha256_of_file(output),
                "blob_count": blob_count,
            }
        )
    )
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    """Restaura el volumen completo desde un `.tar.gz` -- DESTRUCTIVO: borra
    el contenido actual de la raiz de almacenamiento antes de extraer.
    Requiere `--confirm` explicito. Valida cada miembro del archivo contra
    traversal (equivalente al `_validate_zip_bounds` de la validacion OOXML,
    Seccion 8 del plan) antes de extraer nada."""
    if not args.confirm:
        print(json.dumps({"error": "se requiere --confirm para una operacion destructiva"}))
        return 1

    root = Path(get_settings().document_storage_root)
    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"error": f"no existe el archivo de entrada: {input_path}"}))
        return 1

    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()

    with tarfile.open(input_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            member_path = (root / member.name).resolve()
            if not member_path.is_relative_to(resolved_root):
                print(json.dumps({"error": f"entrada de archivo fuera de la raiz: {member.name}"}))
                return 1

        # Se vacia el CONTENIDO de la raiz, nunca la raiz misma: cuando
        # `root` es el punto de montaje de un volumen Docker nombrado (el
        # caso real de este bloque), el propio SO rechaza `rmdir` sobre el
        # punto de montaje ("Device or resource busy") aunque ya este vacio.
        _clear_directory_contents(root)

        tar.extractall(root, filter="data")  # miembros ya validados arriba, uno por uno
        blob_count = sum(1 for m in members if m.isfile())

    print(json.dumps({"root": str(root), "blob_count": blob_count}))
    return 0


def _cmd_verify(_args: argparse.Namespace) -> int:
    """Recalcula el SHA-256 del 100% de los blobs registrados como ACTIVE,
    SUPERSEDED o DELETED (Seccion 14 del plan: verificacion completa, nunca
    una muestra) y lo compara contra `sha256_hash` en BD. Un blob DELETED
    ausente no es una anomalia (retencion fisica diferida es opcional, la
    purga es un proceso futuro fuera de alcance); un blob ACTIVE/SUPERSEDED
    ausente si lo es."""
    root = Path(get_settings().document_storage_root)
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(DeclarationDocumentFile).where(
                    DeclarationDocumentFile.status.in_(_RETAINED_STATUSES)
                )
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    mismatches: list[dict] = []
    missing: list[dict] = []
    verified_ok = 0

    for row in rows:
        blob_path = root / row.storage_key
        if not blob_path.exists():
            if row.status == "DELETED":
                continue
            missing.append({"file_id": str(row.id), "status": row.status})
            continue
        actual = _sha256_of_file(blob_path)
        if actual != row.sha256_hash:
            mismatches.append({"file_id": str(row.id), "status": row.status})
            continue
        verified_ok += 1

    result = {
        "total_rows": len(rows),
        "verified_ok": verified_ok,
        "mismatches": mismatches,
        "missing_blobs": missing,
    }
    print(json.dumps(result))
    return 0 if not mismatches and not missing else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archivar/restaurar/verificar el volumen de almacenamiento documental"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser("archive", help="Empaqueta el volumen en un .tar.gz")
    archive_parser.add_argument("--output", required=True, help="Ruta de salida del .tar.gz")
    archive_parser.set_defaults(func=_cmd_archive)

    extract_parser = subparsers.add_parser("extract", help="Restaura el volumen desde un .tar.gz")
    extract_parser.add_argument("--input", required=True, help="Ruta del .tar.gz a extraer")
    extract_parser.add_argument(
        "--confirm", action="store_true", help="Confirma la operacion destructiva"
    )
    extract_parser.set_defaults(func=_cmd_extract)

    verify_parser = subparsers.add_parser(
        "verify", help="Recalcula el SHA-256 del 100%% de los blobs registrados"
    )
    verify_parser.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
