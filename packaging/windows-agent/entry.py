"""Punto de entrada de PyInstaller del Agente SOL (Domain G). Solo delega en `windows_agent.__main__.main`."""

from windows_agent.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
