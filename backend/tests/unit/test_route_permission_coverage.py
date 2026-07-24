"""Cobertura estructural de rutas (Bloque 4, ADR-0052, Seccion 7 del plan):
recorre las rutas reales de FastAPI (no una lista aproximada) y falla si un
endpoint no exento no declara `require_permission`, si el permiso declarado
no es miembro real de `Permission`, o si una ruta de `/api/v1/users` no
requiere especificamente `user:read`/`user:manage`. No necesita Docker/DB."""

from app.core.permissions import Permission
from app.main import app

EXEMPT_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/session-policy"),
        ("POST", "/api/v1/auth/keep-alive"),
        ("GET", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/auth/sessions/revoke-others"),
        ("POST", "/api/v1/auth/change-password"),
        ("GET", "/api/v1/health/live"),
        ("GET", "/api/v1/health/ready"),
    }
)


def _find_required_permission(dependant, seen=None):
    if seen is None:
        seen = set()
    if id(dependant) in seen:
        return None
    seen.add(id(dependant))

    call = getattr(dependant, "call", None)
    permission = getattr(call, "required_permission", None)
    if permission is not None:
        return permission

    for sub in getattr(dependant, "dependencies", []):
        found = _find_required_permission(sub, seen)
        if found is not None:
            return found
    return None


def _api_v1_routes():
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api/v1/"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods - {"HEAD", "OPTIONS"}:
            yield method, path, route


def test_every_non_exempt_route_declares_a_valid_permission() -> None:
    checked = 0
    for method, path, route in _api_v1_routes():
        if (method, path) in EXEMPT_ROUTES:
            continue
        permission = _find_required_permission(route.dependant)
        assert permission is not None, (
            f"{method} {path} no declara require_permission ni esta en EXEMPT_ROUTES"
        )
        assert isinstance(permission, Permission), f"{method} {path} usa un permiso invalido"
        checked += 1
    assert checked >= 90, f"se esperaban al menos 90 rutas gateadas, se revisaron {checked}"


def test_users_routes_require_user_read_or_user_manage() -> None:
    checked = 0
    for method, path, route in _api_v1_routes():
        if not path.startswith("/api/v1/users"):
            continue
        permission = _find_required_permission(route.dependant)
        assert permission in (Permission.USER_READ, Permission.USER_MANAGE), (
            f"{method} {path} debe requerir user:read o user:manage, tiene {permission}"
        )
        checked += 1
    assert checked == 6, f"se esperaban 6 rutas bajo /api/v1/users, se encontraron {checked}"


def test_exempt_routes_all_exist_as_real_routes() -> None:
    real_routes = {(method, path) for method, path, _route in _api_v1_routes()}
    for exempt in EXEMPT_ROUTES:
        assert exempt in real_routes, f"la ruta exenta {exempt} ya no existe en la app real"
