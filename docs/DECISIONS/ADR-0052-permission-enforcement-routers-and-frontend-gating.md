# ADR-0052: `require_permission` aplicado en routers, cobertura estructural verificada, gateo frontend como UX

## Estado
Aceptado

## Contexto
Con el catálogo de permisos definido (ADR-0050), quedaba decidir dónde se aplica la comprobación de
autorización, cómo se garantiza que ningún endpoint queda sin protección al agregarse en el futuro, y qué
papel juega el frontend en todo esto. `ensure_admin_role` (ADR-0047) era el único precedente: un control
puntual en un solo router, sin mecanismo de cobertura ni generalización.

## Decisión

### Dependencia única `require_permission`, aplicada en routers
`require_permission(permission)` (`backend/app/core/permissions.py`) devuelve un `Depends` que primero
resuelve `require_password_ready` (ADR-0051) y luego comprueba `has_permission(current_user.role,
permission)`, lanzando 403 en caso contrario. Se aplica en la firma de cada endpoint de los 15 routers
existentes (`Depends(require_permission(Permission.X))`), nunca dentro de un servicio — mismo principio que
ADR-0037 (los servicios no conocen al usuario HTTP autenticado como concepto de autorización, esa
responsabilidad es del router). `ensure_admin_role` se elimina: su único punto de uso
(`declarations.py::close_declaration` para `force_close_with_pending`) se reescribe para depender de
`Permission.DECLARATION_CLOSE` a nivel de ruta y comprobar `Permission.DECLARATION_CLOSE_OVERRIDE` de forma
condicional dentro del handler (porque depende del payload, no puede expresarse como un `Depends` estático).

Cada checker devuelto por `require_permission` queda etiquetado (`checker.required_permission = permission`)
para ser introspectable sin ejecutar la petición.

### Cobertura estructural verificada por prueba, no por convención
`backend/tests/unit/test_route_permission_coverage.py` recorre `app.routes` recorriendo recursivamente
`route.dependant.dependencies` en busca del atributo etiquetado. Una allowlist explícita y cerrada
`EXEMPT_ROUTES: frozenset[tuple[method, path]]` (10 pares método+ruta reales: los 8 endpoints de autoservicio
de `auth.py` más los 2 de `health.py`) es la única fuente de exención — nunca un prefijo abierto. Cualquier
endpoint nuevo bajo `/api/v1/` que no declare `require_permission` y no esté en `EXEMPT_ROUTES` hace fallar la
prueba, incluidos endpoints futuros de `auth.py` que alguien agregue sin actualizar la allowlist a propósito.
Complementada por `test_permission_matrix_api.py` (30 casos, uno por permiso): confirma 403 real para el rol
sin el permiso y ausencia de 403 para el rol con el permiso, aprovechando que FastAPI resuelve todos los
`Depends()` (incluido `require_permission`) antes de validar el cuerpo de la petición — confirmado
empíricamente ejecutando los 30 casos con cuerpos vacíos/inválidos y UUIDs ficticios.

### Gateo frontend: capa de UX, nunca de seguridad
`frontend/src/lib/permissions.ts` expone `hasPermission(user, permission)` como función pura de una línea
sobre `user.permissions` (calculado por el backend, ver ADR-0050); no existe ningún mapa de roles duplicado
en TypeScript. Todo control mutante de los 16 componentes/páginas existentes (formularios de creación,
botones de cambio de estado, anulaciones, exportaciones con efecto lateral) se oculta o deshabilita según
`hasPermission`, con mensaje explicativo cuando el control queda deshabilitado en vez de oculto. Esto reduce
la fricción de UX (un usuario sin permiso no ve botones que terminarían en 403) pero **no sustituye** la
comprobación del backend: cada endpoint sigue protegido independientemente del estado visual del cliente,
verificable invocándolo directamente.

## Alternativas consideradas
- **Verificar permisos dentro de cada servicio**: rechazado por la misma razón que ADR-0037 — mezclaría
  lógica de autorización HTTP con lógica de dominio pura, dificultando probar los servicios de forma aislada.
- **Exención por prefijo (`path.startswith("/api/v1/auth")`)**: rechazado explícitamente — un prefijo abierto
  exime automáticamente cualquier endpoint futuro bajo ese prefijo sin que nadie lo decida conscientemente;
  la allowlist cerrada obliga a una decisión explícita por cada ruta nueva.
- **Confiar solo en el gateo visual del frontend**: rechazado — un cliente HTTP directo (curl, Postman, un
  frontend modificado) podría invocar cualquier endpoint sin pasar por la UI; la única barrera real es el
  backend.

## Consecuencias
- `test_route_permission_coverage.py` y `test_permission_matrix_api.py` corren en cada suite completa; un
  router nuevo sin `require_permission` rompe la build antes de llegar a revisión humana.
- Los 16 componentes/páginas de la Sección 8 del plan de Bloque 4 (contribuyentes, obligaciones, categorías
  tributarias, periodos, cronogramas, generación de declaraciones, panel de declaración completo, documentos,
  determinaciones, pagos tributarios, honorarios, cierre, `Sidebar`, `/users`) consumen `hasPermission` desde
  `useCurrentUser()`.
- Documentado en `docs/SECURITY_MODEL.md` y `docs/USER_MANUAL.md` la distinción explícita entre gateo de UX
  (frontend) y control de acceso real (backend).
