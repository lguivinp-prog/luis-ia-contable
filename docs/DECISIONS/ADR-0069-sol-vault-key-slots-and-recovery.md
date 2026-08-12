# ADR-0069: Bóveda SOL — key slots, recuperación en dos pasos e invitaciones de dos actores

## Estado
Aceptado

## Contexto
Cada bóveda necesita sobrevivir a que un usuario olvide su contraseña personal, y necesita permitir que un
segundo usuario del despacho obtenga su propio acceso sin que la contraseña de nadie se comparta en claro.
Esta decisión documenta el diseño de `sol_vault_key_slots` y `sol_vault_invitations`, y el fe de erratas de
recuperación descubierto en el spike (`recovery_confirmed_at` vive en el slot, no en la bóveda).

## Decisión

### `sol_vault_key_slots`: dos tipos, con las mismas garantías de envoltura
Cada slot (`kind IN ('USER', 'RECOVERY')`) envuelve su propia copia de `vault_root_key` bajo un KEK derivado
de su propio secreto y su propia sal — nunca comparte ciphertext con otro slot. Un slot `USER` está atado a
`user_id`; un slot `RECOVERY` tiene `user_id IS NULL` (no pertenece a una persona, pertenece a la bóveda).
Índices únicos parciales garantizan, a nivel de PostgreSQL: como máximo un slot `USER` activo por
`(vault_id, user_id)`; como máximo un slot `RECOVERY` activo **confirmado**; como máximo un slot `RECOVERY`
activo **pendiente** — de modo que un reemplazo de recovery en curso nunca puede colisionar con otro.

### Recuperación en dos pasos, con el código mostrado una sola vez
1. **Inicializar o reemplazar** (`initialize_vault` / `replace_recovery`): genera un código de recuperación
   aleatorio (32 bytes, hex) y lo devuelve en la respuesta HTTP **una única vez** — `recovery_code` no se
   persiste en ninguna forma reversible, solo el resultado de envolver `vault_root_key` bajo el KEK derivado
   de ese código. Ningún endpoint posterior puede volver a mostrarlo.
2. **Confirmar** (`confirm_recovery`): el usuario reingresa el código; el servicio deriva el KEK, intenta
   desenvolver `vault_root_key` con él, y solo si la autenticación GCM tiene éxito marca
   `recovery_confirmed_at`. Esto verifica criptográficamente que el código mostrado es realmente el que
   funciona — nunca una comparación de texto contra un valor guardado.

**Fe de erratas del spike, corregida aquí desde el diseño inicial**: `recovery_confirmed_at` vive
exclusivamente en la fila del slot `RECOVERY` — **nunca** en `sol_vaults`. La bóveda expone
`recovery_confirmed` como un campo derivado en `SolVaultStatusOut`, calculado consultando si existe un slot
`RECOVERY` activo con `recovery_confirmed_at IS NOT NULL`. Guardar ese booleano también en `sol_vaults` habría
creado dos fuentes de verdad que podrían desincronizarse (por ejemplo, tras un reemplazo de recovery que crea
un slot nuevo sin confirmar mientras el anterior seguía confirmado).

### Reemplazo de recovery: coexistencia hasta la confirmación
`replace_recovery` crea un slot `RECOVERY` nuevo **sin revocar todavía el anterior confirmado** — ambos
coexisten hasta que `confirm_recovery` revoca el anterior, en la misma transacción de confirmación. Si el
reemplazo se pierde antes de confirmarse (el usuario nunca vuelve), el código anterior sigue funcionando: no
hay ninguna ventana en la que la bóveda quede sin ningún código de recuperación utilizable.

### `recover`: transición `RECOVERY_REQUIRED -> ACTIVE`, nunca revela ninguna clave
Cuando el último slot `USER` activo se revoca sin que quede otro (`revoke_slot`), la bóveda pasa a
`RECOVERY_REQUIRED`. `recover` exige el código de recuperación confirmado y una contraseña personal nueva:
revoca el slot `USER` anterior del actor (si existía), crea uno nuevo, y emite una autorización de desbloqueo
en memoria (`VaultUnlockRegistry.issue`, ver ADR-0071) — la respuesta pública nunca incluye
`vault_root_key` ni ningún derivado reversible.

### Invitaciones de dos actores, con asociación efímera nunca persistida
`sol_vault_invitations` registra `invited_by`/`target_user_id`/`status`/`expires_at`, pero el paso crítico —
que el invitador ceda temporalmente acceso a su `vault_root_key` ya desenvuelta para envolver una copia nueva
para el invitado — nunca toca la base de datos. `InvitationAssociationRegistry` (memoria de proceso, mismo
ciclo de vida que `VaultUnlockRegistry`) mapea `invitation_id -> handle_del_invitador`. `accept_invitation`
resuelve ese handle contra el registro real: si el invitador ya se bloqueó, si su sesión expiró, o si nunca
tuvo `SLOT_MANAGEMENT` en sus operaciones permitidas (el caso de un `contador` invitando, ya que
`_CONTADOR_OPERATIONS` no incluye `SLOT_MANAGEMENT`), la aceptación falla con
`SOL_VAULT_INVITATION_INVITER_NOT_UNLOCKED` — nunca hay una ruta en la que el invitado obtenga acceso sin que
el invitador esté genuinamente desbloqueado en ese instante. `reauthorize_invitation` permite renovar esa
asociación tras un reinicio del backend (que la destruye, igual que destruye todo el `VaultUnlockRegistry`)
sin tener que cancelar y recrear la invitación completa.

Estados de invitación: `PENDING`, `ACCEPTED`, `EXPIRED`, `CANCELLED`. Un intento de crear una invitación
mientras ya existe una `PENDING` no vencida para el mismo destinatario falla con
`SOL_VAULT_INVITATION_ALREADY_PENDING`; si la pendiente ya venció, se marca `EXPIRED` automáticamente y se
crea la nueva sin fricción.

## Alternativas consideradas
- **Guardar `recovery_confirmed` como columna de `sol_vaults`**: rechazada explícitamente — es la fe de
  erratas que este ADR corrige; crearía una segunda fuente de verdad.
- **Persistir la asociación de invitación cifrada en base de datos**: rechazada — equivaldría a guardar una
  copia de `vault_root_key` fuera del control de un slot con su propio secreto, debilitando la garantía de que
  cada acceso depende únicamente del secreto de su propio dueño.
- **Permitir codigo de recuperación reutilizable indefinidamente sin reemplazo**: no se impone una expiración
  de tiempo al código, pero el mecanismo de reemplazo (`replace_recovery`) existe precisamente para permitir
  rotarlo por decisión operativa.

## Consecuencias
- Un reinicio del backend invalida toda asociación de invitación pendiente de aceptar — el invitador debe
  `reauthorize_invitation` tras volver a desbloquear su propia bóveda.
- La prueba de migración (`test_sol_vault_migration_constraints.py`) verifica los 3 índices únicos parciales
  por nombre exacto contra `pg_indexes`, no solo contra el modelo ORM.
- ADR-0071 documenta el registro en memoria (`VaultUnlockRegistry`) sobre el que se apoyan tanto la emisión de
  autorizaciones de `recover`/`unlock` como la asociación efímera de invitaciones.
