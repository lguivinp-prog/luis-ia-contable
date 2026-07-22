# Resumen ejecutivo — Luis IA Contable

## Qué es

Luis IA Contable es una plataforma local-first para que un contador independiente peruano centralice el control de obligaciones tributarias, laborales, documentales y de honorarios profesionales de su cartera de contribuyentes.

## Para quién

- **Uso inicial**: un único usuario (el contador), ~20 contribuyentes, persona natural y jurídica, todos los regímenes tributarios peruanos.
- **Uso futuro**: multiusuario y multidespacho (otros contadores, cada uno con su propia cartera aislada).

## Qué resuelve

- Evita omisiones de vencimientos tributarios y laborales.
- Centraliza el estado documental, de presentación, de pago del impuesto y de honorarios por cada obligación y periodo.
- Genera recordatorios (Google Calendar) y mensajes asistidos (WhatsApp) sin automatizar el envío.
- Organiza documentos en una estructura estandarizada en Google Drive.
- Clasifica alertas institucionales (Gmail) para revisión humana.
- Mantiene una bitácora de auditoría completa de toda actuación relevante.

## Qué NO hace (por diseño, no por limitación técnica)

- No presenta declaraciones tributarias.
- No realiza pagos ni gestiona fraccionamientos de forma automática.
- No modifica datos del RUC, ni da de alta/baja trabajadores.
- No sustituye el criterio profesional del contador.
- No evade CAPTCHAs ni mecanismos de seguridad de portales oficiales.

## Datos operativos confirmados

| Parámetro | Valor |
|---|---|
| País | Perú |
| Idioma | Español |
| Moneda | Soles (PEN) |
| Zona horaria | America/Lima |
| Formato de fecha visible | DD/MM/YYYY |
| Equipo principal | Windows 11 Pro, 64 bits, 32 GB RAM |
| Horario habitual de operación | 08:00–22:00 |
| Contribuyentes iniciales | ~20 (personas naturales y jurídicas) |

## Alcance de esta entrega

Esta entrega cubre **Fase 0 (fundamentos)** y el **primer bloque de Fase 1 (núcleo operativo)**: autenticación local, gestión de contribuyentes, validación y consulta simulada de RUC, catálogo de obligaciones tributarias y asignación por cliente, y auditoría básica. El resto de módulos (Google, Gmail, WhatsApp, bóveda de credenciales SOL, automatización supervisada de SUNAT, IA) están diseñados en la documentación pero se implementan en fases posteriores. Ver [ROADMAP.md](ROADMAP.md).
