import pytest

from app.core.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permission_codes_for_role,
)


class _FakeUser:
    def __init__(self, role: str) -> None:
        self.role = role


def test_permission_enum_has_30_values() -> None:
    assert len(list(Permission)) == 30


def test_admin_has_every_permission() -> None:
    assert ROLE_PERMISSIONS["admin"] == frozenset(Permission)


def test_contador_lacks_user_management_and_close_override() -> None:
    contador_permissions = ROLE_PERMISSIONS["contador"]
    assert Permission.USER_READ not in contador_permissions
    assert Permission.USER_MANAGE not in contador_permissions
    assert Permission.DECLARATION_CLOSE_OVERRIDE not in contador_permissions
    assert len(contador_permissions) == 27


def test_asistente_is_read_only_plus_document_item_write_and_reports() -> None:
    asistente_permissions = ROLE_PERMISSIONS["asistente"]
    assert len(asistente_permissions) == 13
    assert Permission.DOCUMENT_ITEM_WRITE in asistente_permissions
    assert Permission.REPORT_EXPORT in asistente_permissions
    assert Permission.DOCUMENT_MANAGE not in asistente_permissions
    assert Permission.CLIENT_WRITE not in asistente_permissions
    assert Permission.TAX_PAYMENT_WRITE not in asistente_permissions
    assert Permission.USER_READ not in asistente_permissions


@pytest.mark.parametrize("role", ["admin", "contador", "asistente"])
def test_every_role_can_read_dashboard(role) -> None:
    assert has_permission(_FakeUser(role), Permission.DASHBOARD_READ)


def test_unknown_role_has_no_permissions() -> None:
    assert permission_codes_for_role("rol-inexistente") == []
    assert not has_permission(_FakeUser("rol-inexistente"), Permission.CLIENT_READ)


def test_permission_codes_for_role_is_sorted_string_list() -> None:
    codes = permission_codes_for_role("admin")
    assert codes == sorted(codes)
    assert all(isinstance(c, str) for c in codes)
    assert "user:manage" in codes
