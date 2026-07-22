# ADR-0003: `tenant_id` en todas las tablas de negocio desde el inicio

## Estado
Aceptado

## Contexto
La aplicación es de un solo usuario hoy, pero debe evolucionar a multiusuario/multidespacho (Fase 6) sin una reescritura mayor del modelo de datos.

## Decisión
Toda tabla que almacene datos de negocio (`users`, `sessions`, `clients`, `client_obligations`, `audit_logs`, y las que se añadan en fases futuras) incluye una columna `tenant_id` no nula, referenciando a `tenants`. Todas las queries de lectura/escritura en `repositories/` filtran explícitamente por el `tenant_id` del usuario autenticado.

## Consecuencias
- En esta fase existe un único `tenant` (creado por el seed), por lo que el filtro por `tenant_id` no cambia el comportamiento observable, pero establece el patrón desde el día uno.
- Cuando se introduzca el segundo tenant (Fase 6), se deben añadir pruebas de integración específicas de aislamiento entre tenants antes de habilitar el registro de nuevos despachos.
- El costo de esta decisión es una columna y un filtro adicional en cada query; se considera bajo comparado con el riesgo de una migración de aislamiento tardía.
