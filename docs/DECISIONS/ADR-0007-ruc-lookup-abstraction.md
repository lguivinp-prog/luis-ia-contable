# ADR-0007: `RucLookupProvider` como puerto desacoplado

## Estado
Aceptado

## Contexto
El sistema debe poder consultar información pública de un RUC, pero no debe acoplarse rígidamente a un proveedor específico (el mercado peruano tiene varias APIs de terceros, y SUNAT no ofrece necesariamente un mecanismo estable y autorizado para todos los datos, en particular el régimen tributario).

## Decisión
Se define una interfaz `RucLookupProvider` (puerto) en `backend/app/domain/ruc_lookup/provider.py`, con:
- `FakeRucLookupProvider`: implementado, determinista, para desarrollo y pruebas, marca sus resultados con `is_simulated=True`.
- `SunatRucLookupProvider`: clase con la misma interfaz, pero cada método lanza `NotImplementedError` explícito — no es una simulación disfrazada de real.

La selección del proveedor activo se hace vía variable de entorno (`RUC_LOOKUP_PROVIDER`), inyectada mediante una fábrica en `core/dependencies.py`.

## Consecuencias
- Cambiar de proveedor real en el futuro (o añadir uno nuevo) no requiere tocar `services/` ni `routers/`, solo añadir un nuevo adaptador.
- El régimen tributario nunca se asume como dato confiable de una API externa: puede registrarse manualmente, verificarse por acceso supervisado (Fase 4), u obtenerse de una fuente oficial cuando exista un mecanismo autorizado — nunca inventado.
- Toda respuesta de un proveedor real deberá compararse explícitamente contra los datos existentes y requerir confirmación del usuario antes de sobrescribir (a implementar junto con el adaptador real).
