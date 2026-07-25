# ADR-0060: Agenda interna y alertas derivadas — semántica única de vencimiento con zona horaria centralizada

## Estado
Aceptado

## Contexto
`settings.timezone = "America/Lima"` existía como string en `config.py` sin usarse activamente antes de este
bloque — es el primer bloque que la necesita de verdad para calcular límites de día correctos. Una primera
versión del diseño tenía dos definiciones de "vencido" incompatibles (una para `OVERDUE` de una tarea
individual, otra para el balde `overdue_tasks`); con tareas que ahora pueden tener hora explícita
(`due_has_time=true`), coexistir ambas definiciones habría producido resultados contradictorios entre la
agenda y el detalle de una tarea.

## Decisión

### Helper centralizado único (`backend/app/core/timezone.py`)
Único punto que interpreta `settings.timezone` (falla explícitamente con `NotImplementedError` si no es
`"America/Lima"`, en vez de asumir silenciosamente un comportamiento incorrecto). Expone
`combine_local_to_utc(fecha, hora|None)` (fin de día 23:59:59 Lima si no hay hora), `start_of_day_utc`,
`end_of_day_utc`, `today_lima`, `next_n_days_end_utc`. Ningún otro servicio instancia `ZoneInfo("America/
Lima")` por su cuenta.

### Semántica única de "vencido", usada sin variante local en toda capa
```python
is_overdue = due_at < clock.utcnow() and status not in ("COMPLETED", "CANCELLED")
```
La misma condición exacta (y los mismos límites de balde `due_today`/`due_next_7_days`, con
`clock.utcnow()` como frontera compartida sin solape) se aplica en `task_agenda_service` (baldes de `GET
/tasks?time_bucket=`, `agenda-summary`, `alerts-summary`), el `task_summary` del dashboard, y nunca se
recalcula en el frontend — que siempre confía en el balde/campo ya resuelto por el backend.

### Filtros canónicos `time_bucket`/`assigned_to` en `GET /tasks`
`time_bucket` acepta únicamente `OVERDUE`/`TODAY`/`NEXT_7_DAYS` (422 ante cualquier otro valor).
`assigned_to` acepta un UUID literal, el literal `"me"` (resuelto en el backend contra el usuario
autenticado, nunca reenviado tal cual a la base de datos) o el literal `"unassigned"` (`IS NULL`) — nunca una
cadena vacía o `null` como señal implícita de "sin responsable". La paginación (`total`/`items`) usa
exactamente los mismos predicados que el filtrado. Los enlaces canónicos del dashboard y de la agenda (
`time_bucket=OVERDUE`, `=TODAY`, `=NEXT_7_DAYS`, `assigned_to=unassigned`, `assigned_to=me`,
`priority=URGENT`) son los únicos puntos de entrada — ninguna vista recalcula localmente a qué balde
pertenece una tarea.

### Alertas derivadas sin tabla propia y sin doble conteo de honorarios
`alert_service`/`task_agenda_service` consultan **únicamente** `operational_tasks` — nunca vuelven a consultar
`professional_fee_risk.py` directamente para el resumen de alertas: una cobranza atrasada ya generó su tarea
`FEE_COLLECTION` (ADR-0059); contarla de nuevo desde el motor de riesgo original duplicaría el mismo hecho.
`source_due_mismatch` (`due_is_overridden=true AND source_due_at <> due_at`) es una dimensión ortogonal a los
3 baldes temporales — puede solaparse intencionalmente (una tarea vencida también puede tener su vencimiento
reprogramado divergente de la fuente).

## Alternativas consideradas
- **Dos definiciones de "vencido" según el contexto**: rechazada al detectar la inconsistencia entre la
  condición de `OVERDUE` individual y el balde `overdue_tasks` — unificadas en una sola expresión.
- **Recalcular baldes en el frontend**: rechazada — duplicaría la lógica de zona horaria en TypeScript,
  con riesgo real de desincronización de un día en los límites (ya observado como clase de bug en el
  desarrollo de este mismo helper).
- **Persistir las 6 categorías de alerta**: rechazada — ver ADR-0057 (mismo principio que excluye tablas de
  estado derivado).

## Consecuencias
- Pruebas con reloj congelado (`frozen_clock`) verifican las fronteras exactas: tarea sin hora a las 23:59:59
  Lima (`due_today`) vs. un segundo después (`overdue`); tarea con hora 10:00 a las 09:59 (`due_today`) vs.
  10:01 (`overdue`); ausencia de solape entre los 3 baldes para el mismo conjunto de tareas.
- El dashboard extiende `GET /dashboard/monthly` de forma aditiva (`task_summary` opcional) — ningún campo
  tributario existente cambia.
