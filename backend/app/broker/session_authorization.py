"""Fase 5, Bloque 5.2, Domain F -- contexto de autorizacion HUMANA de una sesion SOL supervisada.

Cuando el operador pulsa "Iniciar acceso SOL", la API registra AQUI, en memoria de PROCESO (junto al broker),
quien inicio la sesion (`user_id`) y con que sesion de aplicacion (`app_session_id`). Nada se persiste y NO contiene
secretos: solo identificadores. Se usa exclusivamente cuando el Agente senala `PREAUTH_READY` (el portal ya fue
validado por completo): el broker revalida y recien entonces llama a `authorize_credential_delivery` (grant de
un solo uso, 60 s). Si el proceso reinicia, el contexto se pierde y la autorizacion falla CERRADA (la sesion se
reintenta con una sesion nueva): jamas se reconstruye una autorizacion desde disco."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

DEFAULT_CONTEXT_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class SessionAuthorization:
    user_id: uuid.UUID
    app_session_id: uuid.UUID


class SessionAuthorizationRegistry:
    def __init__(self, *, ttl_seconds: float = DEFAULT_CONTEXT_TTL_SECONDS, clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[uuid.UUID, uuid.UUID], tuple[SessionAuthorization, float]] = {}

    def register(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, user_id: uuid.UUID, app_session_id: uuid.UUID
    ) -> None:
        with self._lock:
            self._entries[(tenant_id, session_id)] = (
                SessionAuthorization(user_id=user_id, app_session_id=app_session_id),
                self._clock() + self._ttl,
            )

    def get(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SessionAuthorization | None:
        with self._lock:
            entry = self._entries.get((tenant_id, session_id))
            if entry is None:
                return None
            context, expires_at = entry
            if self._clock() >= expires_at:
                del self._entries[(tenant_id, session_id)]
                return None
            return context

    def discard(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> None:
        with self._lock:
            self._entries.pop((tenant_id, session_id), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# Estados de credencial elegibles para un acceso supervisado. `NEEDS_VALIDATION` es el estado inicial y esta
# elegible para el PRIMER acceso supervisado (ADR-0068: la validacion real ocurre por acceso supervisado); `ACTIVE`
# normal. INVALID/LOCKED_BY_SUNAT/PASSWORD_EXPIRED/DISABLED se rechazan. Este bloque NO promueve a ACTIVE.
USABLE_CREDENTIAL_STATUSES = frozenset({"NEEDS_VALIDATION", "ACTIVE"})
