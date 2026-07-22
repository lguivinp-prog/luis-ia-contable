# Pruebas de stack completo

Esta carpeta contiene pruebas que se ejecutan **contra el stack real** ya levantado con `docker compose up` (no pruebas unitarias/integración de código, que viven junto a cada aplicación, p. ej. `backend/tests/`).

- `smoke/test_stack_smoke.ps1`: valida con peticiones HTTP reales que el backend y el frontend respondan correctamente tras `scripts/start.ps1`.

## Cómo ejecutar

```powershell
.\scripts\start.ps1
.\tests\smoke\test_stack_smoke.ps1
```
