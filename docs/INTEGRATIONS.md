# Integraciones — Luis IA Contable

## Estado en esta entrega

| Integración | Estado | Notas |
|---|---|---|
| Consulta de RUC | Interfaz + adaptador simulado | `RucLookupProvider` (puerto) + `FakeRucLookupProvider` (implementado) + `SunatRucLookupProvider` (stub, `NotImplementedError`) |
| Google Calendar | No implementado | Diseño en el encargo original; Fase 2 |
| Google Drive | No implementado | Fase 2 |
| Gmail | No implementado | Fase 3 |
| WhatsApp (asistido) | No implementado | Fase 2 |
| WhatsApp Cloud API | No implementado, deshabilitado hasta contar con número comercial | Fase 6 |
| Bóveda de credenciales SOL | No implementado | Fase 4 |
| Automatización supervisada de SUNAT (Playwright) | No implementado | Fase 4 |
| IA (clasificación, resúmenes, redacción asistida) | No implementado | Fase 3+, siempre con `AIProvider` desacoplado y `DisabledAIProvider` por defecto |

## Patrón de integración: puerto + adaptador(es)

Todas las integraciones externas siguen el mismo patrón, ya aplicado en `domain/ruc_lookup/`:

1. Una interfaz (`Protocol`/ABC) que define el contrato, independiente del proveedor concreto.
2. Un adaptador simulado/fake para desarrollo y pruebas, siempre determinista.
3. Un adaptador real, que puede no estar implementado todavía — en ese caso lanza `NotImplementedError` explícito, nunca simula funcionar.
4. Selección del adaptador vía configuración (`.env`), no hardcodeada.

Este patrón se repetirá para `WhatsAppProvider` (`AssistedWhatsAppProvider`, `WhatsAppCloudApiProvider`), `AIProvider` (`DisabledAIProvider`, `ManualAIProvider`, `AnthropicAIProvider`) y los clientes de Google (Calendar/Drive/Gmail) cuando se implementen.

## `RucLookupProvider` (implementado en esta entrega)

Ubicación: `backend/app/domain/ruc_lookup/`.

- `provider.py`: define `RucLookupProvider` (Protocol) y el DTO `RucLookupResult` (incluye `is_simulated: bool`).
- `fake_provider.py`: `FakeRucLookupProvider` — genera datos deterministas a partir del RUC de entrada (misma entrada → misma salida), sin llamadas de red, marcado `is_simulated=True`.
- `sunat_provider.py`: `SunatRucLookupProvider` — clase con la misma interfaz; cada método lanza `NotImplementedError("Proveedor real de consulta de RUC aún no implementado; ver docs/ROADMAP.md")`.
- Selección vía `RUC_LOOKUP_PROVIDER` en `.env` (`fake` por defecto).

Cuando se implemente el proveedor real (probablemente una API peruana de terceros), deberá incorporar: token almacenado como secreto (no en texto plano), caché temporal, control de cuota, reintentos controlados, y comparación explícita con los datos existentes antes de sobrescribir (confirmación del usuario).

## Notas para integraciones futuras

- **Google (Calendar/Drive/Gmail)**: OAuth 2.0, nunca contraseña directa. Alcance mínimo necesario. Un solo correo Gmail configurado.
- **WhatsApp asistido**: el sistema prepara el mensaje y abre WhatsApp con el texto precargado; el envío siempre lo confirma manualmente el usuario. Nunca se automatiza el envío desde el WhatsApp personal.
- **IA**: nunca recibe credenciales, tokens ni contraseñas en el prompt. Todo contenido externo (correos, PDFs, documentos) se trata como dato no confiable, no como instrucción.
