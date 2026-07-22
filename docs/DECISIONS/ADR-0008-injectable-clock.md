# ADR-0008: Reloj inyectable para pruebas temporales

## Estado
Aceptado

## Contexto
El bloqueo por intentos fallidos (ver ADR-0009) y la expiración de sesiones por inactividad/absoluta requieren pruebas automatizadas que ejerciten el paso del tiempo (p. ej. "han pasado 16 minutos desde el bloqueo"). Usar `time.sleep()` real en las pruebas fue descartado explícitamente por el usuario: haría la suite lenta y frágil.

## Decisión
Se introduce `backend/app/core/clock.py`, un módulo con una función a nivel de módulo `utcnow()` que delega en una referencia mutable (`_current`), más `set_clock(fn)`/`reset_clock()` para pruebas. El código de producción siempre importa el módulo (`from app.core import clock`) y llama a `clock.utcnow()` — nunca `from app.core.clock import utcnow`, porque esa forma capturaría la función original antes de que una prueba la reemplace con `monkeypatch`/fixture, rompiendo el mecanismo de inyección.

La fixture `frozen_clock` (`backend/tests/conftest.py`) expone un diccionario `{"now": datetime}` mutable: las pruebas avanzan el reloj asignando `frozen_clock["now"] += timedelta(...)`.

## Alcance
Se retrofitó únicamente en el código nuevo de este bloque (`auth_service.py`, `login_attempt_service.py`, `tax_schedule_service.py`, `tax_schedule_import_service.py`). El código preexistente del Bloque 1 no se tocó salvo que una prueba nueva lo requiriera explícitamente.

## Consecuencias
- Las pruebas de bloqueo/expiración pasan en milisegundos, sin `sleep`.
- Cualquier código nuevo que dependa de "ahora" debe usar `clock.utcnow()`, no `datetime.now(UTC)` directamente, o quedará fuera del alcance de `frozen_clock`.
