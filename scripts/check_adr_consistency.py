#!/usr/bin/env python3
"""Valida la consistencia de referencias ADR-NNNN en todo el repositorio.

Falla (exit code != 0) cuando:
  - una referencia ADR-NNNN en codigo/migraciones/pruebas/documentacion no
    tiene un archivo fisico docs/DECISIONS/ADR-NNNN-*.md correspondiente;
  - existen dos archivos docs/DECISIONS/ con el mismo numero;
  - el numero en el encabezado ("# ADR-NNNN: ...") de un archivo no coincide
    con el numero de su propio nombre de archivo.

No requiere dependencias externas ni Docker: se ejecuta directamente con
Python 3 desde la raiz del repositorio.

Uso:
    python scripts/check_adr_consistency.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISIONS_DIR = REPO_ROOT / "docs" / "DECISIONS"

SCAN_DIRS = [
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "backend" / "migrations",
    REPO_ROOT / "backend" / "tests",
    REPO_ROOT / "docs",
    REPO_ROOT / "frontend" / "src",
]
SCAN_EXTENSIONS = {".py", ".md", ".ts", ".tsx"}

# Coincide con citas simples ("ADR-0016") y con grupos abreviados que
# reutilizan el prefijo una sola vez ("ADR-0017/0018/0022/0028",
# "ADR-0016..0018"): despues del primer numero con prefijo "ADR-", acepta
# numeros de 4 digitos sueltos separados por "/" o ".." sin repetir "ADR-".
CITATION_GROUP_RE = re.compile(r"ADR-(\d{4})((?:(?:/|\.\.)\d{4})*)")
BARE_NUMBER_RE = re.compile(r"\d{4}")
FILENAME_RE = re.compile(r"^ADR-(\d{4})-.+\.md$")
HEADER_RE = re.compile(r"^#\s*ADR-(\d{4}):")


def find_referenced_numbers() -> dict[str, set[Path]]:
    """Devuelve {numero_de_4_digitos: {archivos que lo citan}}."""
    referenced: dict[str, set[Path]] = {}
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for match in CITATION_GROUP_RE.finditer(text):
                numbers = {match.group(1)} | set(BARE_NUMBER_RE.findall(match.group(2)))
                for number in numbers:
                    referenced.setdefault(number, set()).add(path)
    return referenced


def find_adr_files() -> dict[str, list[Path]]:
    """Devuelve {numero_de_4_digitos: [archivos docs/DECISIONS/ con ese numero en el nombre]}."""
    by_number: dict[str, list[Path]] = {}
    if not DECISIONS_DIR.exists():
        return by_number
    for path in sorted(DECISIONS_DIR.glob("ADR-*.md")):
        match = FILENAME_RE.match(path.name)
        if not match:
            continue
        by_number.setdefault(match.group(1), []).append(path)
    return by_number


def header_number(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = HEADER_RE.match(stripped)
        return match.group(1) if match else None
    return None


def main() -> int:
    referenced = find_referenced_numbers()
    adr_files = find_adr_files()

    errors: list[str] = []

    # 1) Toda referencia debe tener un archivo fisico unico.
    for number, files in sorted(referenced.items()):
        matches = adr_files.get(number, [])
        if not matches:
            citing = ", ".join(str(f.relative_to(REPO_ROOT)) for f in sorted(files)[:5])
            errors.append(
                f"ADR-{number}: referenciado sin archivo fisico en docs/DECISIONS/. "
                f"Citado en: {citing}"
            )

    # 2) Ningun numero debe tener mas de un archivo.
    for number, paths in sorted(adr_files.items()):
        if len(paths) > 1:
            names = ", ".join(p.name for p in paths)
            errors.append(f"ADR-{number}: {len(paths)} archivos con el mismo numero: {names}")

    # 3) El numero del encabezado debe coincidir con el numero del nombre de archivo.
    for number, paths in sorted(adr_files.items()):
        for path in paths:
            actual = header_number(path)
            if actual is None:
                errors.append(f"{path.name}: no se encontro un encabezado '# ADR-NNNN: ...' valido")
            elif actual != number:
                errors.append(
                    f"{path.name}: el nombre de archivo indica ADR-{number} pero el "
                    f"encabezado dice ADR-{actual}"
                )

    if errors:
        print(f"FALLO: {len(errors)} inconsistencia(s) de ADR detectada(s):\n")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"OK: {len(adr_files)} archivos ADR, {len(referenced)} numeros referenciados, "
        "todas las referencias resueltas a un archivo unico y coherente."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
