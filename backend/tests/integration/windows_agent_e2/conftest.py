"""Fase 5, Bloque 5.2, Domain E2 -- reexpone las fixtures ya productivas de
`tests/integration/broker/conftest.py` (Domain D/E1-B) para las pruebas
del Agente Windows -- mismo patron estandar de pytest para reutilizar
fixtures entre directorios de prueba, nunca una copia duplicada de la
logica de arranque del broker/boveda/PostgreSQL real."""

from __future__ import annotations

from tests.integration.broker.conftest import (  # noqa: F401
    _reset_vault_unlock_registry,
    broker_ca,
    e1b_setup,
    pairing_key,
    running_broker,
    session_factory,
)
