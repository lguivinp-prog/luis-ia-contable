from app.services.user_service import removes_last_active_admin


class _FakeUser:
    def __init__(self, role: str, is_active: bool) -> None:
        self.role = role
        self.is_active = is_active


def test_deactivating_the_only_active_admin_is_blocked() -> None:
    target = _FakeUser("admin", True)
    assert removes_last_active_admin(target, [target], new_role=None, deactivating=True) is True


def test_deactivating_an_admin_when_another_admin_remains_is_allowed() -> None:
    target = _FakeUser("admin", True)
    other_admin = _FakeUser("admin", True)
    assert (
        removes_last_active_admin(target, [target, other_admin], new_role=None, deactivating=True)
        is False
    )


def test_demoting_the_only_active_admin_is_blocked() -> None:
    target = _FakeUser("admin", True)
    assert removes_last_active_admin(target, [target], new_role="contador", deactivating=False) is True


def test_demoting_an_admin_when_another_admin_remains_is_allowed() -> None:
    target = _FakeUser("admin", True)
    other_admin = _FakeUser("admin", True)
    assert (
        removes_last_active_admin(target, [target, other_admin], new_role="asistente", deactivating=False)
        is False
    )


def test_changing_role_of_an_inactive_admin_never_reduces_active_count() -> None:
    target = _FakeUser("admin", False)
    # target no aparece entre los admins activos bloqueados, por definicion
    assert removes_last_active_admin(target, [], new_role="contador", deactivating=False) is False


def test_promoting_non_admin_to_admin_never_reduces_active_count() -> None:
    target = _FakeUser("contador", True)
    other_admin = _FakeUser("admin", True)
    assert (
        removes_last_active_admin(target, [other_admin], new_role="admin", deactivating=False) is False
    )


def test_no_op_role_change_to_same_role_is_not_flagged_by_predicate() -> None:
    # El rechazo por "mismo rol" ocurre ANTES de llegar al predicado (422 en
    # el servicio); si igualmente se evaluara, "admin"->"admin" no cuenta
    # como remocion de rol/actividad.
    target = _FakeUser("admin", True)
    assert removes_last_active_admin(target, [target], new_role="admin", deactivating=False) is False


def test_changing_role_of_inactive_admin_with_active_admins_present_is_not_blocked() -> None:
    """Regresion dedicada (ver Seccion 3 del plan): un admin inactivo nunca
    cuenta como el 'ultimo' admin activo, incluso cuando el tenant tiene
    margen de sobra de admins activos."""
    target = _FakeUser("admin", False)
    active_admin_1 = _FakeUser("admin", True)
    active_admin_2 = _FakeUser("admin", True)
    assert (
        removes_last_active_admin(
            target, [active_admin_1, active_admin_2], new_role="contador", deactivating=False
        )
        is False
    )
