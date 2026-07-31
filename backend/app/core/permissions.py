import enum

from fastapi import Depends, HTTPException, status

from app.core.dependencies import get_current_user
from app.models.user import User


class Permission(str, enum.Enum):
    """Catalogo cerrado de permisos (ver ADR-0050). Cualquier valor fuera de
    este enum no puede construirse -- has_permission/require_permission
    fallan de forma cerrada ante un rol o permiso desconocido, nunca
    permiten por defecto."""

    USER_READ = "user:read"
    USER_MANAGE = "user:manage"
    CLIENT_READ = "client:read"
    CLIENT_WRITE = "client:write"
    OBLIGATION_READ = "obligation:read"
    OBLIGATION_WRITE = "obligation:write"
    TAX_CATEGORY_READ = "tax_category:read"
    TAX_CATEGORY_WRITE = "tax_category:write"
    TAX_PERIOD_READ = "tax_period:read"
    TAX_PERIOD_MANAGE = "tax_period:manage"
    TAX_SCHEDULE_READ = "tax_schedule:read"
    TAX_SCHEDULE_MANAGE = "tax_schedule:manage"
    DECLARATION_READ = "declaration:read"
    DECLARATION_WRITE = "declaration:write"
    DECLARATION_FILE = "declaration:file"
    DECLARATION_CLOSE = "declaration:close"
    DECLARATION_CLOSE_OVERRIDE = "declaration:close_override"
    DOCUMENT_READ = "document:read"
    DOCUMENT_ITEM_WRITE = "document:item_write"
    DOCUMENT_MANAGE = "document:manage"
    DOCUMENT_FILE_MANAGE = "document:file_manage"
    TAX_ASSESSMENT_READ = "tax_assessment:read"
    TAX_ASSESSMENT_WRITE = "tax_assessment:write"
    TAX_PAYMENT_READ = "tax_payment:read"
    TAX_PAYMENT_WRITE = "tax_payment:write"
    TAX_PAYMENT_VOID = "tax_payment:void"
    PROFESSIONAL_FEE_READ = "professional_fee:read"
    PROFESSIONAL_FEE_WRITE = "professional_fee:write"
    PROFESSIONAL_FEE_VOID = "professional_fee:void"
    REPORT_EXPORT = "report:export"
    DASHBOARD_READ = "dashboard:read"
    TASK_READ = "task:read"
    TASK_CREATE = "task:create"
    TASK_UPDATE = "task:update"
    TASK_COMPLETE = "task:complete"
    TASK_ASSIGN = "task:assign"
    TASK_CANCEL = "task:cancel"
    TASK_SYNC = "task:sync"
    ALERT_READ = "alert:read"
    # ---- Fase 3, Bloque 3.1: integracion Google OAuth (identidad) ----
    INTEGRATION_CONNECT = "integration:connect"
    INTEGRATION_DISCONNECT = "integration:disconnect"


_READ_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.CLIENT_READ,
        Permission.OBLIGATION_READ,
        Permission.TAX_CATEGORY_READ,
        Permission.TAX_PERIOD_READ,
        Permission.TAX_SCHEDULE_READ,
        Permission.DECLARATION_READ,
        Permission.DOCUMENT_READ,
        Permission.TAX_ASSESSMENT_READ,
        Permission.TAX_PAYMENT_READ,
        Permission.PROFESSIONAL_FEE_READ,
        Permission.DASHBOARD_READ,
        Permission.TASK_READ,
        Permission.ALERT_READ,
        Permission.TASK_SYNC,
        Permission.INTEGRATION_CONNECT,
        Permission.INTEGRATION_DISCONNECT,
    }
)

_CONTADOR_ONLY_ADDITIONS: frozenset[Permission] = frozenset(
    {
        Permission.CLIENT_WRITE,
        Permission.OBLIGATION_WRITE,
        Permission.TAX_CATEGORY_WRITE,
        Permission.TAX_PERIOD_MANAGE,
        Permission.TAX_SCHEDULE_MANAGE,
        Permission.DECLARATION_WRITE,
        Permission.DECLARATION_FILE,
        Permission.DECLARATION_CLOSE,
        Permission.DOCUMENT_MANAGE,
        Permission.DOCUMENT_FILE_MANAGE,
        Permission.TAX_ASSESSMENT_WRITE,
        Permission.TAX_PAYMENT_WRITE,
        Permission.TAX_PAYMENT_VOID,
        Permission.PROFESSIONAL_FEE_WRITE,
        Permission.PROFESSIONAL_FEE_VOID,
        Permission.TASK_ASSIGN,
        Permission.TASK_CANCEL,
    }
)

_ASISTENTE_EXTRA_ADDITIONS: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_ITEM_WRITE,
        Permission.REPORT_EXPORT,
        Permission.TASK_CREATE,
        Permission.TASK_UPDATE,
        Permission.TASK_COMPLETE,
    }
)

# ROLE_PERMISSIONS es la unica fuente de verdad del reparto rol->permisos (ver
# ADR-0050). El frontend nunca reconstruye esta matriz: consume la lista ya
# calculada por permission_codes_for_role() a traves de GET /auth/me.
ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "admin": frozenset(Permission),
    "contador": _READ_PERMISSIONS | _CONTADOR_ONLY_ADDITIONS | _ASISTENTE_EXTRA_ADDITIONS,
    "asistente": _READ_PERMISSIONS | _ASISTENTE_EXTRA_ADDITIONS,
}


def permission_codes_for_role(role: str) -> list[str]:
    """Lista ordenada de codigos de permiso efectivos para un rol. Rol
    desconocido -> lista vacia (fallo cerrado), nunca todos los permisos."""
    permissions = ROLE_PERMISSIONS.get(role, frozenset())
    return sorted(p.value for p in permissions)


def has_permission(user: User, permission: Permission) -> bool:
    if not isinstance(permission, Permission):
        return False  # fallo cerrado ante un permiso que no es miembro real del enum
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def check_permission(user: User, permission: Permission) -> None:
    """Verificacion directa (no dependencia) para el caso condicional dentro
    de un handler -- ver `close_declaration` (force_close_with_pending),
    donde el permiso requerido depende del payload y no puede expresarse
    como un `Depends` estatico de la ruta."""
    if not has_permission(user, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Esta accion requiere el permiso '{permission.value}'",
        )


def require_password_ready(current_user: User = Depends(get_current_user)) -> User:
    """Bloquea toda ruta gateada por permiso mientras el usuario tenga un
    cambio de contrasena obligatorio pendiente (ver ADR-0051). Las rutas de
    `auth.py` no pasan por aqui -- por eso `/me`, `/change-password`,
    `/logout`, `/keep-alive`, `/sessions*` siguen accesibles y permiten
    completar el cambio obligatorio."""
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PASSWORD_CHANGE_REQUIRED"},
        )
    return current_user


def require_permission(permission: Permission):
    """Fabrica de dependencia FastAPI que generaliza `ensure_admin_role` a
    cualquier permiso del catalogo (ver ADR-0052). Se aplica en routers,
    nunca en servicios de dominio -- misma regla que ADR-0047."""

    def _checker(current_user: User = Depends(require_password_ready)) -> User:
        check_permission(current_user, permission)
        return current_user

    _checker.required_permission = permission  # etiqueta introspectable (ver prueba estructural)
    return _checker
