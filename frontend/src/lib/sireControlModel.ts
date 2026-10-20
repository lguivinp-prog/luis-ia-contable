// M4 -- modelo puro del control SIRE: etiquetas, validacion previa al envio (el backend la repite), formato de importes SIN float y mensajes de error. Un cuadre numerico
// NO es una presentacion oficial ante SUNAT: ningun texto de este modulo lo afirma.
import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";
import type { SireComponent, SireControl, SireCurrency, SireDecision, SireOrigin, SireSource, SireSummaryCode } from "@/lib/types/sireControl";

export function canReadSireControl(user: User | null | undefined): boolean {
  return hasPermission(user, "sire_control:read");
}

export function canWriteSireControl(user: User | null | undefined): boolean {
  return hasPermission(user, "sire_control:write");
}

export const COMPONENT_LABEL: Record<SireComponent, string> = {
  RVIE: "RVIE — Registro de Ventas e Ingresos Electrónicos",
  RCE: "RCE — Registro de Compras Electrónico",
};

export const ORIGIN_LABEL: Record<SireOrigin, string> = {
  SUNAT: "SUNAT (datos atribuidos a SUNAT)",
  DARWIN: "DARWIN / CONTASIS (sistema contable)",
};

export const CONCEPT_LABEL: Record<string, string> = {
  record_count: "Cantidad de registros",
  base_amount: "Base / importe comparable",
  igv_amount: "IGV",
  total_amount: "Importe total",
};

export const DECISION_LABEL: Record<SireDecision, string> = {
  APPROVED: "Verificado por el contador",
  OBSERVED: "Con observaciones",
  REQUIRES_REGULARIZATION: "Diferencias por regularizar",
};

export type Tone = "ok" | "warn" | "danger" | "muted" | "info";

export interface SummaryInfo {
  label: string;
  tone: Tone;
  hint: string;
}

const SUMMARY: Record<SireSummaryCode, SummaryInfo> = {
  NO_CONTROL: { label: "Sin expediente", tone: "muted", hint: "Aún no se incorporó información de este registro." },
  NO_SOURCES: { label: "Sin fuentes", tone: "muted", hint: "El expediente no tiene fuentes vigentes." },
  PARTIAL: { label: "Información parcial", tone: "warn", hint: "Falta una fuente o hay conceptos que solo existen en una de ellas; la comparación queda pendiente." },
  NOT_COMPARABLE: { label: "No comparable", tone: "warn", hint: "Las fuentes no comparten conceptos comparables (o están en monedas distintas)." },
  MATCH: { label: "Cuadra", tone: "ok", hint: "Los conceptos comparables coinciden. Falta tu revisión profesional." },
  DIFFERENCES: { label: "Presenta diferencias", tone: "danger", hint: "Hay diferencias entre las fuentes. No se corrigen automáticamente." },
  MATCH_REVIEWED: { label: "Cuadra · revisado", tone: "ok", hint: "Cuadra y fue revisado por el contador." },
  DIFFERENCES_REVIEWED: { label: "Diferencias · revisado", tone: "warn", hint: "Hay diferencias y el contador registró su decisión." },
  NEEDS_REVIEW: { label: "Requiere nueva revisión", tone: "warn", hint: "Una fuente o la tolerancia cambió después de la última revisión." },
};

export function summaryInfo(code: string | null | undefined): SummaryInfo {
  return SUMMARY[(code ?? "NO_CONTROL") as SireSummaryCode] ?? SUMMARY.NO_CONTROL;
}

export const OFFICIAL_FILING_NOTICE =
  "Este control no es una constancia oficial de presentación: un cuadre numérico o una revisión no prueban que los registros estén presentados ante SUNAT. Verifica la presentación en SUNAT.";

// --- importes (cadenas decimales; nunca number) ---------------------------------------------------------------------------------

const AMOUNT_RE = /^-?\d{1,13}(\.\d{1,2})?$/;

/** "11800.5" -> "11,800.50". Operacion SOLO sobre la cadena: ningun redondeo ni aritmetica de punto flotante. */
export function formatMoney(value: string | null | undefined, currency: SireCurrency | string = "PEN"): string {
  if (value === null || value === undefined || value === "") return "—";
  if (!AMOUNT_RE.test(value)) return value;
  const negative = value.startsWith("-");
  const [integer, decimals = ""] = (negative ? value.slice(1) : value).split(".");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const symbol = currency === "USD" ? "US$" : "S/";
  return `${negative ? "−" : ""}${symbol} ${grouped}.${decimals.padEnd(2, "0")}`;
}

export function formatDifference(value: string | null | undefined, concept: string): string {
  if (value === null || value === undefined) return "—";
  if (concept === "record_count") return value.startsWith("-") ? `−${value.slice(1)}` : value === "0" ? "0" : `+${value}`;
  if (!AMOUNT_RE.test(value)) return value;
  const isZero = /^-?0+(\.0+)?$/.test(value);
  if (isZero) return "0.00";
  return value.startsWith("-") ? `−${formatMoney(value.slice(1), "PEN").replace("S/ ", "")}` : `+${formatMoney(value, "PEN").replace("S/ ", "")}`;
}

export function formatConceptValue(concept: string, value: string | null, currency: SireCurrency | string): string {
  if (value === null) return "desconocido";
  return concept === "record_count" ? value : formatMoney(value, currency);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --- validacion previa al envio ---------------------------------------------------------------------------------------------------

export interface ManualForm {
  recordCount: string;
  baseAmount: string;
  igvAmount: string;
  totalAmount: string;
  sourceLabel: string;
  sourceDate: string;
  notes: string;
}

export type FieldErrors = Partial<Record<keyof ManualForm | "form", string>>;

export function normalizeAmountInput(raw: string): string | null {
  const text = raw.trim().replace(/\s+/g, "");
  if (text === "") return null;
  return text;
}

/** Valida SIN convertir a number: los importes son cadenas decimales con hasta 2 decimales; un campo vacio es DESCONOCIDO (no cero). */
export function validateManualForm(form: ManualForm, today: string): FieldErrors {
  const errors: FieldErrors = {};
  const count = form.recordCount.trim();
  if (count !== "" && !/^\d{1,10}$/.test(count)) errors.recordCount = "Ingresa un entero mayor o igual a 0.";
  for (const [key, value] of [["baseAmount", form.baseAmount], ["igvAmount", form.igvAmount], ["totalAmount", form.totalAmount]] as const) {
    const normalized = normalizeAmountInput(value);
    if (normalized !== null && !AMOUNT_RE.test(normalized)) errors[key] = "Usa punto decimal y hasta 2 decimales (p. ej. 1180.50).";
  }
  if (count === "" && [form.baseAmount, form.igvAmount, form.totalAmount].every((v) => v.trim() === "")) {
    errors.form = "Ingresa al menos un dato: lo que dejes vacío queda como desconocido, nunca como cero.";
  }
  if (form.sourceLabel.trim() === "") errors.sourceLabel = "Identifica la fuente (p. ej. «Reporte RVIE septiembre»).";
  else if (form.sourceLabel.trim().length > 120) errors.sourceLabel = "Máximo 120 caracteres.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.sourceDate)) errors.sourceDate = "Indica la fecha de la fuente.";
  else if (form.sourceDate > today) errors.sourceDate = "La fecha de la fuente no puede ser futura.";
  if (form.notes.length > 2000) errors.notes = "Máximo 2000 caracteres.";
  return errors;
}

export function hasErrors(errors: FieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

// --- reglas de revision (el backend las vuelve a exigir) ----------------------------------------------------------------------------

export function allowedDecisions(control: Pick<SireControl, "comparison">): SireDecision[] {
  const status = control.comparison.status;
  if (status === "NO_SOURCES") return [];
  return status === "MATCH" ? ["APPROVED", "OBSERVED", "REQUIRES_REGULARIZATION"] : ["OBSERVED", "REQUIRES_REGULARIZATION"];
}

export function decisionNeedsNotes(decision: SireDecision): boolean {
  return decision !== "APPROVED";
}

export function sourceFor(control: SireControl, origin: SireOrigin): SireSource | null {
  return origin === "SUNAT" ? control.sunat_source : control.darwin_source;
}

// --- mensajes de error -----------------------------------------------------------------------------------------------------------------

const ERROR_MESSAGES: Record<string, string> = {
  CLIENT_NOT_FOUND: "El cliente no existe o no pertenece a tu despacho.",
  PERIOD_NOT_FOUND: "El periodo no existe o no pertenece a tu despacho.",
  PERIOD_NOT_MONTHLY: "El control SIRE solo aplica a periodos mensuales.",
  CONTROL_NOT_FOUND: "El expediente no existe.",
  FILE_NOT_FOUND: "El archivo no existe en este expediente.",
  FILE_MISSING: "El archivo ya no está en el almacenamiento: avisa al administrador.",
  SOURCE_NOT_FOUND: "La fuente indicada no pertenece a este expediente.",
  SOURCE_HAS_NO_DATA: "Ingresa al menos un dato: lo desconocido no se asume como cero.",
  SOURCE_LABEL_REQUIRED: "Identifica la fuente (reporte o exportación).",
  SOURCE_DATE_IN_FUTURE: "La fecha de la fuente no puede ser futura.",
  SOURCE_CHANGED: "La fuente vigente cambió mientras trabajabas. Recarga el expediente antes de reemplazarla.",
  COMPARISON_CHANGED: "La comparación cambió desde que la viste (nueva fuente o tolerancia). Recarga antes de revisar.",
  VERSION_CONFLICT: "El expediente cambió. Recarga e inténtalo de nuevo.",
  IDEMPOTENCY_CONFLICT: "Esta solicitud ya se usó con datos distintos. Recarga la página.",
  AMOUNT_INVALID: "Hay un importe inválido (usa hasta 2 decimales; no se redondea).",
  NOTES_TOO_LONG: "Las observaciones son demasiado largas.",
  REVIEW_APPROVAL_REQUIRES_MATCH: "Solo se puede verificar un control que cuadra. Usa «Con observaciones» o «Por regularizar».",
  REVIEW_NOTES_REQUIRED: "Esta decisión exige una observación.",
  REVIEW_NOTHING_TO_REVIEW: "Aún no hay fuentes que revisar.",
  REVIEW_DECISION_INVALID: "Decisión inválida.",
  TOLERANCE_REASON_REQUIRED: "Una tolerancia exige indicar el motivo (queda visible y auditada).",
  FILE_TOO_LARGE: "El archivo supera el tamaño máximo permitido.",
  FILE_EMPTY: "El archivo está vacío.",
  FILE_TYPE_NOT_ALLOWED: "Formato no permitido. Usa CSV, XLSX, PDF, JPEG o PNG.",
  FILE_NOT_TABULAR: "Solo CSV y XLSX se pueden importar; los demás formatos se conservan como evidencia sin interpretarse.",
  FILE_ALREADY_IMPORTED: "Este archivo ya se importó como una fuente del expediente.",
  FILE_NOT_IMPORTABLE: "Este archivo ya no se puede importar.",
  FILE_INTEGRITY_FAILED: "El archivo almacenado no coincide con su huella SHA-256: no se importó.",
  IMPORT_NO_HEADERS: "La primera fila no contiene encabezados.",
  IMPORT_BLANK_HEADER: "Hay columnas con encabezado vacío: complétalo o elimina la columna.",
  IMPORT_AMBIGUOUS_HEADERS: "Hay encabezados repetidos o ambiguos. Renómbralos en el archivo.",
  IMPORT_TOO_MANY_COLUMNS: "El archivo tiene demasiadas columnas.",
  IMPORT_TOO_MANY_ROWS: "El archivo supera el máximo de filas permitido.",
  IMPORT_BINARY_CONTENT: "El archivo no es un CSV de texto.",
  IMPORT_ENCODING_UNSUPPORTED: "No se pudo leer la codificación (usa UTF-8 o Windows-1252).",
  IMPORT_DELIMITER_UNDETECTED: "No se detectó un delimitador consistente (coma, punto y coma, tabulador o barra vertical).",
  IMPORT_EMPTY: "El archivo no tiene filas de datos.",
  IMPORT_CSV_MALFORMED: "El CSV está mal formado.",
  IMPORT_XLSX_FORMULA: "El libro contiene fórmulas: pega los valores y vuelve a exportar.",
  IMPORT_XLSX_ACTIVE_CONTENT: "El libro contiene macros, vínculos externos u objetos incrustados: expórtalo como XLSX simple o CSV.",
  IMPORT_XLSX_CORRUPT: "No se pudo abrir el libro XLSX.",
  IMPORT_SHEET_REQUIRED: "El libro tiene varias hojas: elige la hoja a importar.",
  IMPORT_SHEET_NOT_FOUND: "La hoja elegida no existe o está oculta.",
  IMPORT_MAPPING_COLUMN_NOT_FOUND: "Una columna asignada ya no existe en el archivo.",
  IMPORT_MAPPING_DUPLICATE_COLUMN: "Una misma columna no puede asignarse a dos campos.",
  IMPORT_MAPPING_FIELD_UNKNOWN: "Campo de mapeo no soportado.",
  IMPORT_MAPPING_EMPTY: "Asigna al menos un campo a comparar.",
  IMPORT_MAPPING_COUNT_IN_DETAIL: "En modo detalle los registros se cuentan por filas: usa el modo resumen para una columna de cantidad.",
  IMPORT_DECIMAL_AMBIGUOUS: "Hay importes con separadores ambiguos (p. ej. 1,234): elige explícitamente punto o coma como separador decimal.",
  IMPORT_ROW_ERRORS: "El archivo contiene filas inválidas; no se importó nada.",
  IMPORT_MODE_INVALID: "Modo de importación inválido.",
  IMPORT_DECIMAL_STYLE_INVALID: "Estilo decimal inválido.",
};

const ROW_ERROR_MESSAGES: Record<string, string> = {
  IMPORT_TOTAL_ROW: "fila de totales no comparable (exclúyela del archivo o confirma ignorar los totales)",
  IMPORT_PERIOD_MISMATCH: "corresponde a otro período",
  IMPORT_PERIOD_INVALID: "el período no se pudo interpretar",
  IMPORT_TYPE_MISMATCH: "es de otro tipo de registro (RVIE/RCE)",
  IMPORT_TYPE_UNRECOGNIZED: "el tipo de registro no es RVIE ni RCE",
  IMPORT_NUMBER_INVALID: "valor no numérico o con más de 2 decimales",
  IMPORT_AMOUNT_EMPTY: "importe vacío (un dato desconocido no se importa como cero)",
  IMPORT_SUMMARY_ROWS: "el modo resumen requiere exactamente una fila de datos",
};

export interface DescribedError {
  message: string;
  rows: string[];
}

export function describeSireError(detail: unknown): DescribedError {
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "code" in detail) {
    const d = detail as { code?: unknown; message?: unknown; errors?: unknown };
    const code = typeof d.code === "string" ? d.code : "";
    const message = ERROR_MESSAGES[code] ?? (typeof d.message === "string" && d.message ? d.message : "No se pudo completar la operación.");
    const rows = Array.isArray(d.errors)
      ? (d.errors as Array<{ row?: number; code?: string }>).slice(0, 10).map((e) => `Fila ${e.row ?? "?"}: ${ROW_ERROR_MESSAGES[e.code ?? ""] ?? "dato inválido"}`)
      : [];
    return { message, rows };
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return { message: "Revisa los datos del formulario: alguno no tiene un formato válido.", rows: [] };
  }
  return { message: "No se pudo completar la operación. Inténtalo de nuevo.", rows: [] };
}

export function errorStatusCode(err: unknown): number | null {
  const status = (err as { status?: unknown } | undefined)?.status;
  return typeof status === "number" ? status : null;
}
