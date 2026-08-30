"""Fase 5, Bloque 5.2, Domain D.1 -- Gate residual (seccion 25 del encargo):
`app.broker.pki.load_or_generate_ca` es SOLO para desarrollo/pruebas locales
-- ninguna ruta de arranque productiva del broker debe invocarla jamas
(fail-closed real: material de CA ausente en produccion debe ser un error de
arranque, nunca una oportunidad de autogenerar silenciosamente). Prueba
ESTRUCTURAL, no una simple aseveracion: inspecciona el codigo fuente REAL de
cada modulo productivo del paquete `app.broker` (nunca los propios tests) en
busca de cualquier referencia al nombre `load_or_generate_ca`."""

from __future__ import annotations

import ast
from pathlib import Path

_BROKER_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "app" / "broker"

_PRODUCTION_MODULES = (
    "__init__.py",
    "connection_registry.py",
    "dispatch.py",
    "errors.py",
    "mtls.py",
    "pending_dispatch.py",
    "protocol.py",
    "server.py",
)


def test_no_production_broker_module_references_load_or_generate_ca():
    offending: list[str] = []
    for filename in _PRODUCTION_MODULES:
        path = _BROKER_PACKAGE_DIR / filename
        assert path.is_file(), f"modulo productivo esperado ausente: {path}"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "load_or_generate_ca":
                offending.append(filename)
            if isinstance(node, ast.Attribute) and node.attr == "load_or_generate_ca":
                offending.append(filename)
    assert offending == [], f"load_or_generate_ca referenciada desde codigo productivo: {offending}"


def test_pki_module_itself_only_defines_it_never_calls_it_internally():
    """`load_or_generate_ca` puede EXISTIR en `pki.py` (la funcion en si,
    documentada como solo-desarrollo/pruebas) pero ninguna OTRA funcion de
    `pki.py` debe invocarla internamente."""
    path = _BROKER_PACKAGE_DIR / "pki.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    calling_functions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "load_or_generate_ca":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id == "load_or_generate_ca":
                    calling_functions.append(node.name)
    assert calling_functions == []
