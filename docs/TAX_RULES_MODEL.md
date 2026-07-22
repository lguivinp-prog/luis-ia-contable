# Modelo de reglas tributarias — Luis IA Contable

## Principio rector

**Nunca se codifican permanentemente en la lógica del sistema fechas, tributos, obligaciones, formularios o multas.** Toda regla tributaria (especialmente los cronogramas de vencimientos de SUNAT) debe:

- Tener una fuente identificable (norma, resolución, portal oficial).
- Tener año de vigencia y versión.
- Tener fecha de incorporación al sistema.
- Poder modificarse sin cambiar código (dato, no código).
- Conservar el historial de versiones anteriores.
- Requerir aprobación humana explícita antes de usarse para generar vencimientos reales.

## Alcance de esta entrega

En este bloque solo existe el **catálogo de obligaciones** (`obligation_catalog`): qué tributos/formularios existen y a qué régimen aplican, cada uno con su `source` (referencia normativa) y `source_version` (fecha/versión del dato). **No existen todavía fechas de vencimiento ni cronogramas** — eso corresponde al "motor de cronogramas tributarios" descrito abajo, que se implementará en un bloque posterior de Fase 1.

## Diseño del motor de cronogramas (fases posteriores, no implementado aún)

Entidades previstas: `tax_schedule_sources`, `tax_schedule_versions`, `tax_schedule_entries`, `tax_schedule_exceptions`. Estados de una versión de cronograma: `borrador` → `importado` → `pendiente_de_revisión` → `aprobado` (o `anulado`); una versión aprobada puede quedar `reemplazado` cuando se importa una posterior.

Flujo obligatorio antes de que un cronograma pueda generar vencimientos:
1. Importar o capturar la fuente (CSV, PDF, texto).
2. Vista previa de los datos importados.
3. Validación de duplicidades.
4. Comparación con la versión previamente aprobada.
5. Presentación de los cambios detectados a un humano.
6. Aprobación explícita (usuario y fecha quedan registrados).
7. Generación de vencimientos únicamente a partir de una versión `aprobado`.
8. La versión anterior se conserva, nunca se sobreescribe.

**Regla dura**: ningún vencimiento se genera desde una tabla de cronograma que no esté en estado `aprobado`.

## Datos semilla de este bloque

El catálogo de obligaciones cargado por `backend/app/seeds/obligation_catalog_data.py` incluye únicamente el **nombre, código y periodicidad** de obligaciones ampliamente conocidas y estables (Formulario 621, PLAME, SIRE-RVIE, SIRE-RCE, Renta Anual 710, ITAN, entre otras), citando la referencia normativa general de cada tributo. No incluye fechas de vencimiento específicas de ningún año — esas se cargarán exclusivamente a través del motor de cronogramas versionado, con el flujo de aprobación descrito arriba.
