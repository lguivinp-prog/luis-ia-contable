# ADR-0049: Reportes CSV/Excel con límite configurable y dependencia declarativa de `openpyxl`

## Estado
Aceptado

## Contexto
El sistema necesita exportar datos operativos (declaraciones, documentos, determinaciones, pagos, honorarios, clientes, auditoría) en formatos que un contador pueda abrir directamente en Excel/Sheets, sin exponerlo a *CSV formula injection* ni generar archivos temporales en disco (que complicarían la limpieza y podrían filtrar datos entre solicitudes concurrentes).

## Decisión
Un único endpoint `POST /api/v1/reports/export` (`app/routers/reports.py`, `app/services/report_service.py`) resuelve 10 tipos de reporte (`DECLARATIONS`, `DECLARATION_DOCUMENTS`, `TAX_ASSESSMENTS`, `TAX_PAYMENTS`, `FEE_AGREEMENTS`, `FEE_CHARGES`, `FEE_PAYMENTS`, `FEE_RECEIPTS`, `CLIENTS`, `AUDIT_LOG`), cada uno con su propia consulta tenant-scoped y columnas fijas.

- **Límite de filas**: `settings.report_export_max_rows` (`int`, por defecto `20000`, configurable por entorno) — la consulta trae `max_rows + 1` filas para detectar truncamiento sin un segundo `COUNT(*)`; la respuesta indica `truncated` en los metadatos.
- **CSV**: `report_service.render_csv` usa `csv.writer` sobre un `io.StringIO` (en memoria) y pasa cada celda por `sanitize_csv_cell` (`app/services/csv_utils.py`, ya usado desde Bloque 2) para neutralizar *CSV formula injection* anteponiendo un apóstrofe a celdas que empiecen con `=`, `+`, `-`, `@`, tab o retorno de carro.
- **Excel**: `report_service.render_xlsx` usa `openpyxl.Workbook(write_only=True)` (modo de solo escritura, más eficiente en memoria) sobre un `io.BytesIO`, con dos hojas: "Datos" (encabezados + filas) y "Metadatos" (tipo de reporte, fecha de generación, usuario, conteo de filas, si se truncó). Nunca se escribe un archivo temporal en disco.
- **Auditoría**: cada exportación registra `REPORT_EXPORT` con `report_type`, `format`, `row_count` y `truncated` en los metadatos, antes del `commit()` único del router.
- **Dependencia declarativa**: `openpyxl==3.1.5` se agregó a `backend/requirements.txt` (nunca instalado manualmente fuera de esa declaración); se verificó reconstruyendo la imagen del backend con `docker compose build --no-cache backend`, confirmando que `pip install -r requirements.txt` la instala por sí sola sin intervención manual.

## Alternativas consideradas
- **Generar el archivo en disco y servirlo como archivo estático**: rechazado — requiere limpieza posterior y crea una ventana donde otro tenant/solicitud podría potencialmente acceder al archivo si la ruta se predice; todo se mantiene en memoria y se descarta al final de la solicitud.
- **Instalar `openpyxl` manualmente en la imagen sin declararlo en `requirements.txt`**: rechazado explícitamente por la instrucción de la fase — cualquier dependencia debe ser reproducible desde `requirements.txt` solamente.

## Consecuencias
- `test_report_service.py` prueba la sanitización CSV contra una celda con fórmula, y que el Excel generado tiene exactamente las hojas "Datos"/"Metadatos" esperadas.
- `test_reports_api.py` prueba los 10 tipos de reporte en ambos formatos, el truncamiento configurable vía `settings.report_export_max_rows`, la auditoría de cada exportación, CSRF, y el rechazo de un tipo de reporte desconocido (422).
- El frontend (`reports/page.tsx`) descarga el blob resultante vía `apiClient.postForFile`, obteniendo el nombre de archivo del encabezado `Content-Disposition` de la respuesta (nunca inventado en el cliente).
