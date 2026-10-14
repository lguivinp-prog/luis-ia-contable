"""Defensa en profundidad del registro de credenciales SOL: los limites del servicio coinciden con las columnas reales y con el esquema de la API."""

import pytest

from app.models.client_sol_credential import ClientSolCredential
from app.schemas.sol_vault import CreateSolCredentialRequest, UpdateSolCredentialRequest
from app.services import client_sol_credential_service as svc


def test_service_limits_match_the_real_column_lengths():
    for field, limit in svc.FIELD_LIMITS.items():
        assert getattr(ClientSolCredential, field).type.length == limit, field


@pytest.mark.parametrize("field", list(svc.FIELD_LIMITS))
def test_service_rejects_values_longer_than_the_column_before_any_database_work(field):
    svc._check_field_lengths(**{field: "x" * svc.FIELD_LIMITS[field]})  # el limite exacto es valido
    with pytest.raises(svc.FieldTooLongError) as raised:
        svc._check_field_lengths(**{field: "x" * (svc.FIELD_LIMITS[field] + 1)})
    assert raised.value.code == "SOL_CREDENTIAL_FIELD_TOO_LONG"


def test_api_schemas_enforce_the_same_limits():
    base = {"credential_kind": "PRIMARY_OWN_RUC", "sol_username": "u", "sol_password": "p", "authorization_basis": "x"}
    CreateSolCredentialRequest(**{**base, "authorization_basis": "x" * 60})
    for field in svc.FIELD_LIMITS:
        with pytest.raises(ValueError):
            CreateSolCredentialRequest(**{**base, field: "x" * (svc.FIELD_LIMITS[field] + 1)})
        with pytest.raises(ValueError):
            UpdateSolCredentialRequest(expected_version=1, **{field: "x" * (svc.FIELD_LIMITS[field] + 1)})
