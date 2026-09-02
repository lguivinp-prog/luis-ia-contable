"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas unitarias de
`app.broker.secret_delivery_grant.SecretDeliveryGrantRegistry`: maquina de
estados atomica, seguridad de recursos, y ausencia de secretos/claves en la
estructura en memoria (prueba U del plan de Domain E1-B)."""

from __future__ import annotations

import uuid

from app.broker.secret_delivery_grant import GrantState, SecretDeliveryGrantRegistry


def _uuids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


def _authorize(registry, *, tenant_id, job_id, agent_id, **overrides):
    kwargs = {
        "tenant_id": tenant_id,
        "session_id": overrides.get("session_id", uuid.uuid4()),
        "job_id": job_id,
        "agent_id": agent_id,
        "user_id": overrides.get("user_id", uuid.uuid4()),
        "app_session_id": overrides.get("app_session_id", uuid.uuid4()),
        "credential_id": overrides.get("credential_id", uuid.uuid4()),
    }
    if "ttl_seconds" in overrides:
        kwargs["ttl_seconds"] = overrides["ttl_seconds"]
    registry.authorize(**kwargs)


def test_A_authorize_then_acquire_transitions_to_in_flight():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)

    grant = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert grant is not None
    assert grant.state == GrantState.IN_FLIGHT
    assert registry.peek(tenant_id=tenant_id, job_id=job_id).state == GrantState.IN_FLIGHT


def test_B_second_acquire_after_first_fails_generic():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)

    first = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    second = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert first is not None
    assert second is None


def test_C_wrong_agent_cannot_acquire_and_never_mutates_legitimate_grant():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, legitimate_agent, attacker_agent = _uuids(4)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=legitimate_agent)

    result = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=attacker_agent)
    assert result is None

    still_there = registry.peek(tenant_id=tenant_id, job_id=job_id)
    assert still_there is not None
    assert still_there.state == GrantState.AUTHORIZED
    assert still_there.agent_id == legitimate_agent

    # el agente legitimo TODAVIA puede adquirir con exito
    legit = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=legitimate_agent)
    assert legit is not None


def test_D_unknown_job_id_never_creates_state():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    assert registry.current_cardinality() == 0
    result = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert result is None
    assert registry.current_cardinality() == 0


def test_E_expired_grant_cannot_be_acquired_and_is_pruned():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id, ttl_seconds=0.0)
    result = registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert result is None
    assert registry.peek(tenant_id=tenant_id, job_id=job_id) is None


def test_F_mark_consumed_removes_entry_irrevocably():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    registry.mark_consumed(tenant_id=tenant_id, job_id=job_id)
    assert registry.peek(tenant_id=tenant_id, job_id=job_id) is None
    # nunca revive -- un nuevo intento de adquisicion falla, no "retorna" nada util
    assert registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id) is None


def test_G_invalidate_removes_entry_never_returns_to_authorized():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    registry.invalidate(tenant_id=tenant_id, job_id=job_id)
    assert registry.peek(tenant_id=tenant_id, job_id=job_id) is None


def test_H_authorize_replaces_never_duplicates_same_key():
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert registry.current_cardinality() == 1


def test_I_bounded_cardinality_never_exceeds_max_entries():
    registry = SecretDeliveryGrantRegistry()
    from app.broker import secret_delivery_grant as module

    original_cap = module._MAX_ENTRIES
    module._MAX_ENTRIES = 5
    try:
        for _ in range(20):
            tenant_id, job_id, agent_id = _uuids(3)
            _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
        assert registry.current_cardinality() <= 5
    finally:
        module._MAX_ENTRIES = original_cap


def test_J_registry_contains_no_secret_or_crypto_key_fields():
    """Prueba U del plan E1-B: inspeccion directa de la estructura en
    memoria -- ningun campo del dataclass `SecretDeliveryGrant` puede ser
    username/password/RUC/clave criptografica."""
    registry = SecretDeliveryGrantRegistry()
    tenant_id, job_id, agent_id = _uuids(3)
    _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    grant = registry.peek(tenant_id=tenant_id, job_id=job_id)
    assert grant is not None

    field_names = set(grant.__dataclass_fields__.keys())
    forbidden_substrings = ("username", "password", "ruc", "root_key", "dek", "secret", "master", "recovery")
    for field_name in field_names:
        lowered = field_name.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), field_name

    # y ningun VALOR de los campos existentes es una cadena de texto
    # sospechosa (todos son UUID/float/str de estado corto conocido)
    for value in (grant.tenant_id, grant.session_id, grant.job_id, grant.agent_id, grant.user_id,
                  grant.app_session_id, grant.credential_id):
        assert isinstance(value, uuid.UUID)
    assert grant.state in (GrantState.AUTHORIZED, GrantState.IN_FLIGHT)


def test_K_clear_removes_everything():
    registry = SecretDeliveryGrantRegistry()
    for _ in range(5):
        tenant_id, job_id, agent_id = _uuids(3)
        _authorize(registry, tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    assert registry.current_cardinality() == 5
    registry.clear()
    assert registry.current_cardinality() == 0
