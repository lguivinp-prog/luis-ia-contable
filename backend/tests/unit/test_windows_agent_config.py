"""Fase 5, Bloque 5.2, Domain E2 -- pruebas AK/AL de la matriz E2 sobre
`windows_agent/config.py`: host del broker no configurable a un valor
remoto, lista blanca de campos persistentes, resolucion del directorio de
datos."""

from __future__ import annotations

import json

import pytest

from windows_agent.config import (
    AgentConfig,
    ConfigError,
    RemoteBrokerHostRejectedError,
    load_config,
    resolve_data_directory,
    save_config,
)


def test_AK_default_config_uses_loopback_host():
    config = AgentConfig()
    assert config.broker_host in ("127.0.0.1", "localhost")


def test_AK_remote_broker_host_rejected():
    """`REMOTE_BROKER_CONFIGURATION_ALLOWED=NO` (seccion 23 del encargo)."""
    with pytest.raises(RemoteBrokerHostRejectedError):
        AgentConfig(broker_host="203.0.113.5")


def test_AK_remote_broker_host_rejected_even_via_saved_config_file(tmp_path):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "agent_config.json").write_text(
        json.dumps({"broker_host": "evil.example.com"}), encoding="ascii"
    )
    with pytest.raises(RemoteBrokerHostRejectedError):
        load_config(tmp_path)


def test_config_round_trip(tmp_path):
    original = AgentConfig(pairing_port=9001, operational_port=9002, read_timeout_seconds=12.5)
    save_config(tmp_path, original)
    reloaded = load_config(tmp_path)
    assert reloaded == original


def test_config_rejects_unknown_persisted_field(tmp_path):
    """Lista blanca de contenido persistente (seccion 12 del encargo) --
    un campo desconocido en el JSON (ej. una credencial colada por error)
    rechaza la carga completa, nunca lo ignora en silencio."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "agent_config.json").write_text(
        json.dumps({"broker_host": "127.0.0.1", "sol_password": "should-never-be-here"}), encoding="ascii"
    )
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_missing_config_file_returns_defaults(tmp_path):
    assert load_config(tmp_path) == AgentConfig()


def test_resolve_data_directory_requires_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ConfigError):
        resolve_data_directory()


def test_resolve_data_directory_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = resolve_data_directory()
    assert data_dir == tmp_path / "LuisIAContable" / "SolAgent"
