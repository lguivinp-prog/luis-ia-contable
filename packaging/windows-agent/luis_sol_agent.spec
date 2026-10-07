# -*- mode: python ; coding: utf-8 -*-
# PyInstaller ONE-FOLDER (nunca one-file: sin autoextraccion a %TEMP% en cada arranque, DLLs inspeccionables,
# upgrade/rollback por carpeta). Dos ejecutables comparten el mismo runtime `_internal`:
#   luis-sol-agent.exe    consola   -> pair / status / version / browser-check (interactivo)
#   luis-sol-agent-w.exe  ventana   -> run (autoarranque al iniciar sesion; sin ventana de consola)
import pathlib

from PyInstaller.utils.hooks import collect_all

HERE = pathlib.Path(SPECPATH)
BACKEND = HERE.parents[1] / "backend"

pw_datas, pw_binaries, pw_hidden = collect_all("playwright")

a = Analysis(
    [str(HERE / "entry.py")],
    pathex=[str(BACKEND)],
    binaries=pw_binaries,
    datas=pw_datas,
    hiddenimports=pw_hidden + ["windows_agent.__main__"],
    excludes=[
        "pytest", "tkinter", "sqlalchemy", "fastapi", "uvicorn", "pydantic", "alembic", "psycopg",
        "argon2", "openpyxl", "tests",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_console = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="luis-sol-agent", console=True, upx=False, debug=False,
)
exe_windowed = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="luis-sol-agent-w", console=False, upx=False, debug=False,
)
coll = COLLECT(exe_console, exe_windowed, a.binaries, a.datas, upx=False, name="app")
