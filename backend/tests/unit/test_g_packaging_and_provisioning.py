"""Domain G: rutas de la instalacion empaquetada, aprovisionamiento EXPLICITO de la CA y diagnostico seguro. Sin red,
sin SUNAT, solo material ficticio generado en tmp_path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.broker import diagnostics, pki, provision
from windows_agent import __main__ as agent_main
from windows_agent import browser_runtime, install_paths


# --- install_paths: build congelada vs desarrollo ---------------------------------------------------------------


def test_not_frozen_never_touches_browser_path(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "ambient-dev-path")
    assert install_paths.is_frozen() is False
    assert browser_runtime.configure_browsers_path() is None
    assert install_paths.installed_browsers_directory() is None
    assert install_paths.read_build_info() is None
    import os

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "ambient-dev-path"


def test_frozen_build_always_uses_its_own_browsers_directory(monkeypatch, tmp_path):
    app = tmp_path / "SolAgentApp" / "app"
    app.mkdir(parents=True)
    (app / "build_info.json").write_text(json.dumps({"version": "9.9.9", "signed": False}), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "luis-sol-agent.exe"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "ambient-dev-path")  # no debe prevalecer
    chosen = browser_runtime.configure_browsers_path()
    assert chosen == tmp_path / "SolAgentApp" / "browsers"
    import os

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(chosen)
    assert install_paths.read_build_info() == {"version": "9.9.9", "signed": False}


def test_installed_browsers_are_separate_from_agent_identity_directory():
    # binarios (SolAgentApp) e identidad (SolAgent) son directorios hermanos DISTINTOS: el upgrade no toca la identidad
    from windows_agent.config import resolve_data_directory

    import os

    os.environ.setdefault("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    assert resolve_data_directory().name == "SolAgent"
    assert "SolAgentApp" not in str(resolve_data_directory())


def test_data_dir_override_and_default_ca_directory(tmp_path):
    class A:
        data_dir = str(tmp_path / "datos")
        ca_dir = None

    assert agent_main._data_dir(A) == tmp_path / "datos"
    expected = "broker_ca"
    assert install_paths.default_ca_directory(Path(A.data_dir)).name == expected
    if sys.platform == "win32":
        assert Path(agent_main._resolve_ca_dir(A, Path(A.data_dir))) == Path(A.data_dir) / expected

    class B:
        data_dir = None
        ca_dir = str(tmp_path / "otra")

    assert agent_main._resolve_ca_dir(B, tmp_path) == str(tmp_path / "otra")


def test_agent_cli_exposes_no_secret_arguments():
    parser = agent_main.build_parser()
    text = parser.format_help()
    for forbidden in ("password", "passphrase", "credential", "secret", "token"):
        assert forbidden not in text.lower()


# --- aprovisionamiento explicito de la CA ----------------------------------------------------------------------


def test_provision_creates_loadable_ca_and_never_overwrites(tmp_path, capsys):
    ca_dir = tmp_path / "pki"
    public = tmp_path / "public"
    result = provision.provision_ca(ca_dir, export_public=public)
    assert (ca_dir / "ca.key.pem").is_file() and (ca_dir / "ca.cert.pem").is_file()
    assert pki.load_ca(ca_dir=ca_dir).certificate is not None  # el broker de producto la puede cargar
    assert result["ca_certificate_sha256"] == pki.fingerprint_sha256_hex(pki.load_ca(ca_dir=ca_dir).certificate)
    # solo el certificado PUBLICO se exporta
    assert (public / "ca.cert.pem").read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert b"PRIVATE KEY" not in (public / "ca.cert.pem").read_bytes()
    assert [p.name for p in public.iterdir()] == ["ca.cert.pem"]
    # nunca se imprime ni se devuelve la clave
    assert "PRIVATE KEY" not in json.dumps(result)
    # segunda ejecucion: rechazo, la CA original queda intacta
    before = (ca_dir / "ca.key.pem").read_bytes()
    with pytest.raises(SystemExit) as exc:
        provision.provision_ca(ca_dir)
    assert "CA_ALREADY_PROVISIONED" in str(exc.value)
    assert (ca_dir / "ca.key.pem").read_bytes() == before


def test_provision_cli_output_never_contains_key_material(tmp_path, capsys):
    assert provision.main(["--ca-dir", str(tmp_path / "pki2"), "--export-public", str(tmp_path / "pub2")]) == 0
    out = capsys.readouterr().out
    assert "ca_certificate_sha256=" in out
    assert "PRIVATE KEY" not in out and "BEGIN" not in out


def test_export_only_copies_certificate_and_fails_without_ca(tmp_path):
    with pytest.raises(SystemExit):
        provision.export_public_certificate(tmp_path / "vacio", tmp_path / "out")
    provision.provision_ca(tmp_path / "pki3")
    target = provision.export_public_certificate(tmp_path / "pki3", tmp_path / "out3")
    assert target.read_bytes() == (tmp_path / "pki3" / "ca.cert.pem").read_bytes()


def test_startup_still_never_autogenerates_the_ca(tmp_path):
    # el aprovisionamiento es un paso EXPLICITO: arrancar sin CA sigue fallando cerrado
    from app.broker.product_startup import BrokerStartupError, start_production_broker
    from app.core.config import Settings

    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/x", session_secret="x" * 40,
                        sol_broker_ca_dir=str(tmp_path / "sin_ca"), sol_broker_enabled=True)
    with pytest.raises(BrokerStartupError):
        start_production_broker(settings, session_factory=None)  # type: ignore[arg-type]
    assert not (tmp_path / "sin_ca").exists()


# --- diagnostico seguro ----------------------------------------------------------------------------------------


def test_diagnostics_report_has_no_secret_material(tmp_path, monkeypatch):
    provision.provision_ca(tmp_path / "pki4")
    from app.core import config

    settings = config.Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/x", session_secret="x" * 40,
                               sol_broker_ca_dir=str(tmp_path / "pki4"), sol_broker_enabled=True)
    monkeypatch.setattr(diagnostics, "get_settings", lambda: settings)
    report = diagnostics.collect()
    text = json.dumps(report)
    assert report["broker_enabled_setting"] is True and report["ca_loadable"] is True
    assert report["ca_key_file_present"] is True  # solo se informa PRESENCIA, nunca contenido
    assert "PRIVATE KEY" not in text and "BEGIN" not in text
    assert "postgresql" not in text and "x" * 40 not in text  # sin DSN ni secretos de configuracion
    assert "database_reachable" in report  # se informa alcanzabilidad; si falla, solo el TIPO del error (nunca el DSN)
    if not report["database_reachable"]:
        assert "database_error_type" in report
