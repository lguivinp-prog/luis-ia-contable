# Block 5.2 — Matriz RBAC

**Domain A — Architecture & Contract Freeze**, confirmada sin cambios
en **Domain A.1 — Persistence & Concurrency Contract Freeze**
(`RBAC_FROZEN=YES`, sección 45 del encargo de Domain A.1 — los 5
permisos, la matriz y el recuento proyectado de esta página se
mantienen exactamente como quedaron congelados en Domain A; ya no es
"borrador"), y confirmada sin cambios de nuevo en **Domain A.2 — Final
DDL Security & Compatibility Gate** (ninguno de los 4 gates de esa
ronda tocó permisos, roles ni la matriz — los 5 permisos `sol_*`
nuevos y el recuento 56/56/48/22 siguen vigentes tal cual). Complementa
`BLOCK5_2_API_CONTRACTS.md`. Sigue el patrón
productivo de
`backend/app/core/permissions.py` (ADR-0050/ADR-0052): catálogo cerrado
`Permission(str, enum.Enum)`, agregado a los `frozenset` de cada rol,
`admin` siempre `frozenset(Permission)` completo (nunca enumerado a
mano), enforcement vía `require_permission(Permission.X)` como
`Depends` en el **router**, nunca en el servicio.

## 1. Estado actual verificado (línea base, antes de este bloque)

```
Permission total (enum completo) = 51
admin     = 51  (frozenset(Permission) completo)
contador  = 44
asistente = 22
```

Verificado directamente contra `backend/app/core/permissions.py` en
esta misma ronda (no asumido de una fuente histórica) — coincide con
lo documentado en ADR-0068 y `CHANGELOG.md`.

## 2. Permisos candidatos de Block 5.2

```python
class Permission(str, enum.Enum):
    ...
    # ---- Fase 5, Bloque 5.2 ----
    SOL_SESSION_CREATE = "sol_session:create"
    SOL_SESSION_READ = "sol_session:read"
    SOL_SESSION_CANCEL = "sol_session:cancel"
    SOL_AGENT_READ = "sol_agent:read"
    SOL_AGENT_MANAGE = "sol_agent:manage"
```

5 permisos nuevos — mismo patrón `resource:action` ya usado por los 9
permisos `sol_vault:*`/`sol_credential:*` de Bloque 5.1.

## 3. Matriz propuesta

| Permiso | admin | contador | asistente |
|---|---|---|---|
| `sol_session:create` | ✅ | ✅ | ❌ |
| `sol_session:read` | ✅ | ✅ | ❌ |
| `sol_session:cancel` | ✅ | ✅ | ❌ |
| `sol_agent:read` | ✅ | ✅ | ❌ |
| `sol_agent:manage` | ✅ | ❌ | ❌ |

**Justificación**, contrastada con el patrón ya usado en Bloque 5.1
(donde `sol_vault:unlock`/`sol_credential:*` son admin+contador, pero
`sol_vault:initialize`/`manage_users`/`recover`/`rotate` — operaciones
de administración estructural del vault — son admin-only):

- **`contador`**: puede **crear/leer/cancelar** sesiones SOL
  supervisadas y **leer** el estado de los agentes disponibles — es el
  rol que en la práctica necesita ejecutar el acceso supervisado día a
  día (mismo nivel de confianza que ya tiene sobre
  `sol_credential:create`/`update` en 5.1). **No** puede gestionar
  agentes (`sol_agent:manage` — pairing/revocación) porque eso es
  administración de infraestructura de confianza (certificados mTLS,
  identidad de máquina), no operación diaria — mismo criterio que ya
  separa `sol_vault:unlock` (contador) de `sol_vault:initialize`
  (admin-only) en 5.1.
- **`asistente`**: **cero permisos nuevos**, igual que en Bloque 5.1
  (donde `asistente` no ganó ningún permiso `sol_vault:*`/
  `sol_credential:*`). El acceso SOL supervisado maneja credenciales
  reales de terceros dentro de una sesión de navegador real — el
  mismo nivel de sensibilidad que ya excluyó a `asistente` de todo
  Bloque 5.1.

**Recuento resultante si se aprueba**:

```
Permission total = 51 + 5 = 56
admin     = 56  (+5, automático por frozenset(Permission))
contador  = 44 + 4 = 48   (+ create, read, cancel, agent:read)
asistente = 22 + 0 = 22   (sin cambio)
```

## 4. `assistant AI` ≠ rol de usuario `"asistente"`

**Distinción explícita, sin ambigüedad en este repo**: `asistente` es
un rol de usuario humano real, persistido (`CheckConstraint` sobre
`users.role`) — el personal junior de un estudio contable. **No existe
hoy** en el código ningún concepto separado de "agente/asistente de
IA" (`assistant`, `Assistant`, `ai_assistant`) — se verificó por
barrido que no hay colisión de nombres.

**El asistente de IA (este mismo agente, Claude/Claude Code) sigue sin
acceso a Phase 5** — no se le concede, en esta ronda ni implícitamente
por ningún permiso de la tabla anterior:

```
acceso al vault SOL
acceso a credenciales SOL
control de sesión SOL
control del navegador contra SUNAT
```

Esta matriz RBAC gobierna usuarios humanos de la aplicación
(`admin`/`contador`/`asistente`) — es una capa completamente distinta
del control de qué puede hacer un asistente de IA dentro de esta
sesión de herramientas. Ninguna fila de la tabla de la sección 3
otorga nada al segundo concepto.
