# ADR-0004: Sin eliminación física de contribuyentes

## Estado
Aceptado

## Contexto
Los contribuyentes (`clients`) son el núcleo de la información contable y de auditoría. Perder un registro por error (accidental o malicioso) puede significar perder trazabilidad legal.

## Decisión
No existe endpoint de `DELETE` físico para `clients`. El ciclo de vida se controla mediante la columna `service_status` (`prospecto`, `activo`, `suspendido`, `pendiente_cierre`, `inactivo`), cambiada vía `PATCH /api/v1/clients/{id}/status`, siempre auditada (`CLIENT_STATUS_CHANGE`).

## Consecuencias
- Un contribuyente que deja de ser cliente pasa a `inactivo`, pero su historial permanece disponible para consulta y auditoría.
- Cualquier necesidad real de purga de datos (por ejemplo, solicitud de un cliente de eliminar sus datos, Fase 6) se maneja como un proceso explícito y documentado, no como un DELETE de API estándar.
