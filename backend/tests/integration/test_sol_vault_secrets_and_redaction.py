"""Ausencia de fuga de secretos y redaccion en errores 422 (Fase 5, Bloque
5.1, ver ADR-0068, Seccion 19). Ningun secreto ficticio usado en un flujo
real (contrasenas, codigo de recuperacion, sol_username/sol_password)
debe aparecer en logs/stdout/stderr capturados, en la representacion de
ninguna excepcion, ni en el cuerpo de una respuesta 422."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import clock as clock_module
from app.core import sol_vault_crypto as crypto
from app.core.security import hash_password
from app.core.sol_vault_registry import VaultUnlockRegistry
from app.models.session import Session as AppSession
from app.models.tenant import Tenant
from app.models.user import User
from app.services import sol_vault_service as svc

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_FAST_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)

_PRODUCTIVE_MODULES = (
    "app/core/sol_vault_crypto.py",
    "app/core/sol_vault_registry.py",
    "app/core/sol_vault_argon2_gate.py",
    "app/services/sol_vault_service.py",
    "app/services/client_sol_credential_service.py",
)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_productive_sol_vault_modules_never_import_logging_or_print():
    for relative_path in _PRODUCTIVE_MODULES:
        source = (_BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
        assert "import logging" not in source, f"{relative_path} no deberia usar logging"
        assert re.search(r"(?<!\w)print\s*\(", source) is None, f"{relative_path} no deberia usar print()"


def test_no_secret_leaks_across_a_full_representative_flow(db_session, monkeypatch, caplog, capsys):
    monkeypatch.setattr(crypto, "production_argon2_params", lambda: _FAST_PARAMS)
    clock_module.set_clock(lambda: _NOW)
    try:
        registry = VaultUnlockRegistry()
        tenant = Tenant(name="Tenant auditoria secretos SOL")
        db_session.add(tenant)
        db_session.flush()
        user = User(
            tenant_id=tenant.id, email=f"actor-{uuid.uuid4().hex[:8]}@sol-vault-tests.dev",
            full_name="Actor auditoria", password_hash=hash_password("TestPassword123!"), role="admin",
            is_active=True,
        )
        db_session.add(user)
        db_session.flush()
        session_row = AppSession(
            tenant_id=tenant.id, user_id=user.id, token_hash=uuid.uuid4().hex,
            csrf_token_hash=uuid.uuid4().hex, created_at=_NOW, last_seen_at=_NOW,
            expires_at=_NOW + timedelta(hours=8),
        )
        db_session.add(session_row)
        db_session.flush()

        password = "contrasena-secreta-de-auditoria-de-fuga"
        wrong_password = "contrasena-incorrecta-de-auditoria"
        wrong_code = "codigo-de-recuperacion-incorrecto-de-auditoria"

        result = svc.initialize_vault(
            db_session, registry, tenant_id=tenant.id, actor=user, app_session_id=session_row.id,
            personal_vault_password=password,
        )
        recovery_code = result.recovery_code
        svc.confirm_recovery(
            db_session, tenant_id=tenant.id, vault_id=result.vault.id, recovery_code=recovery_code
        )

        collected_exception_texts: list[str] = []
        try:
            svc.unlock(
                db_session, VaultUnlockRegistry(), tenant_id=tenant.id, vault_id=result.vault.id, actor=user,
                app_session_id=session_row.id, personal_vault_password=wrong_password,
            )
        except svc.BadVaultPasswordError as exc:
            collected_exception_texts.append(str(exc))
            collected_exception_texts.append(repr(exc))

        try:
            svc.confirm_recovery(
                db_session, tenant_id=tenant.id, vault_id=result.vault.id, recovery_code=wrong_code
            )
        except svc.BadVaultPasswordError as exc:
            collected_exception_texts.append(str(exc))
            collected_exception_texts.append(repr(exc))

        secrets_that_must_never_leak = [password, wrong_password, recovery_code, wrong_code]

        captured = capsys.readouterr()
        haystacks = [("stdout", captured.out), ("stderr", captured.err), ("caplog", caplog.text)]
        haystacks.extend(("exception_text", text) for text in collected_exception_texts)

        for secret in secrets_that_must_never_leak:
            for source_name, haystack in haystacks:
                assert secret not in haystack, (
                    f"el secreto ficticio {secret!r} aparecio en {source_name} -- fuga de secretos detectada"
                )
    finally:
        clock_module.reset_clock()


def test_422_on_initialize_never_echoes_the_rejected_password(auth_client):
    """La contrasena rechazada por la validacion de longitud (Seccion 19)
    nunca debe aparecer en el cuerpo de la respuesta 422 -- Pydantic solo
    debe informar el codigo/mensaje de error, nunca ecoar el valor
    original en el `input` reflejado."""
    too_short_password = "corta-secreta"
    assert len(too_short_password) < 14  # confirma que dispara la validacion de longitud minima

    response = auth_client.post(
        "/api/v1/sol-vault/initialize", json={"personal_vault_password": too_short_password}
    )
    assert response.status_code == 422
    assert too_short_password not in response.text


def test_422_on_unlock_never_echoes_the_password_when_extra_field_rejected(auth_client):
    """`extra=forbid` en los esquemas hace que un campo inesperado dispare
    422 -- incluso en ese caso, ningun valor de contrasena provisto en la
    misma peticion debe aparecer reflejado en el cuerpo de la respuesta."""
    secret_value = "clave-personal-ficticia-que-nunca-debe-ecoarse"
    response = auth_client.post(
        "/api/v1/sol-vault/unlock",
        json={"personal_vault_password": secret_value, "unexpected_field": secret_value},
    )
    assert response.status_code == 422
    assert secret_value not in response.text
