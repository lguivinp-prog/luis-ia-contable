"""M2 -- parser PURO de la Ficha RUC (sin navegador, sin red). Recibe filas (rotulo, lineas de valor) ya extraidas del
frame de la Ficha y produce campos estructurados normalizados.

REGLAS (nada se inventa):
* solo se interpretan rotulos de la lista blanca `CAPTIONS` (igualdad EXACTA tras normalizar acentos/mayusculas/dos puntos);
  un rotulo desconocido se ignora, un campo ausente queda ausente (nunca un valor por defecto);
* un valor mal formado (fecha invalida, RUC que no son 11 digitos) NO se aproxima: el campo se omite y se reporta en
  `malformed`;
* secciones opcionales ausentes no son error; la ausencia del RUC SI lo es (identidad del contribuyente).

ESTADO DE VALIDACION EN VIVO: la estructura de FILAS (3 celdas: rotulo | ":" | valor) y los rotulos de `LIVE_VALIDATED_CAPTIONS` se
observaron en vivo (sonda saneada, aceptacion M2). El RUC NO esta en una fila de rotulo: se toma del encabezado oficial "FICHA RUC: <RUC>"
(`identify_ruc_from_header`), unico y en su posicion estructural observada; si no se puede, se falla cerrado. La bandera global sigue en
False hasta confirmar el encabezado con una lectura real."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

SCHEMA_VERSION = 1
CAPTIONS_LIVE_VALIDATED = False
MAX_ROWS = 300
MAX_LINE_LENGTH = 300


def normalize_caption(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    stripped = re.sub(r"\s+", " ", stripped).strip().lower()
    return stripped.rstrip(":").strip()


# rotulo normalizado -> clave de campo del esquema v1
CAPTIONS: dict[str, str] = {
    "numero de ruc": "ruc_y_razon_social",
    "tipo contribuyente": "tipo_contribuyente",
    "nombre comercial": "nombre_comercial",
    "fecha de inscripcion": "fecha_inscripcion",
    "fecha de inicio de actividades": "fecha_inicio_actividades",
    "estado del contribuyente": "estado_contribuyente",
    "condicion del contribuyente": "condicion_contribuyente",
    "domicilio fiscal": "domicilio_fiscal",
    "sistema emision de comprobante": "sistema_emision_comprobante",
    "actividad comercio exterior": "actividad_comercio_exterior",
    "actividad de comercio exterior": "actividad_comercio_exterior",  # rotulo real observado en vivo
    "sistema de contabilidad": "sistema_contabilidad",  # rotulo real observado en vivo
    "sistema contabilidad": "sistema_contabilidad",
    "actividad(es) economica(s)": "actividades_economicas",
    "comprobantes de pago c/aut. de impresion (f. 806 u 816)": "comprobantes_pago",
    "sistema de emision electronica": "sistema_emision_electronica",
    "emisor electronico desde": "emisor_electronico_desde",
    "comprobantes electronicos": "comprobantes_electronicos",
    "afiliado al ple desde": "afiliado_ple_desde",
    "padrones": "padrones",
    # Variantes CANDIDATAS (no validadas en vivo) de rotulos que el contribuyente podria ver con otra redaccion; cada una
    # termina en una clave existente del esquema v1 y solo se acepta si la fila trae un valor interpretable.
    "apellidos y nombres o razon social": "razon_social_caption",
    "tipo de contribuyente": "tipo_contribuyente",
    "condicion de domicilio fiscal": "condicion_contribuyente",
    "actividad economica principal": "actividad_principal",
    "actividades economicas secundarias": "actividades_secundarias",
    "sistema emision comprobantes de pago": "sistema_emision_comprobante",
    # Dependencia y domicilio fiscal ESTRUCTURADO (rotulos del Reporte oficial de Ficha RUC; su aparicion exacta en la pantalla viva se
    # confirma con la sonda: lo que no coincida simplemente no se extrae).
    "dependencia sunat": "dependencia_sunat",
    "departamento": "dom_departamento",
    "provincia": "dom_provincia",
    "distrito": "dom_distrito",
    "tipo y nombre zona": "dom_zona",
    "tipo y nombre via": "dom_via",
    "nro": "dom_numero",
    "km": "dom_km",
    "mz": "dom_manzana",
    "lote": "dom_lote",
    "dpto": "dom_dpto",
    "interior": "dom_interior",
    "otras referencias": "dom_referencias",
    "condicion del inmueble declarado como domicilio fiscal": "dom_condicion_inmueble",
}
_DOMICILIO_KEYS = {
    "dom_departamento": "departamento", "dom_provincia": "provincia", "dom_distrito": "distrito", "dom_zona": "zona", "dom_via": "via",
    "dom_numero": "numero", "dom_km": "km", "dom_manzana": "manzana", "dom_lote": "lote", "dom_dpto": "dpto", "dom_interior": "interior",
    "dom_referencias": "referencias", "dom_condicion_inmueble": "condicion_inmueble",
}
CAPTION_FORMS: list[str] = sorted(CAPTIONS)

# Rotulos observados EN VIVO (la sonda registro rotulos genericos; ver docs/CHANGELOG): estos si estan validados contra el portal real.
LIVE_VALIDATED_CAPTIONS = frozenset({
    "apellidos y nombres o razon social", "tipo de contribuyente", "fecha de inscripcion", "fecha de inicio de actividades",
    "estado del contribuyente", "condicion de domicilio fiscal", "emisor electronico desde", "comprobantes electronicos",
    "nombre comercial", "actividad economica principal", "sistema emision comprobantes de pago", "sistema de contabilidad",
    "actividad de comercio exterior",
})
# Filas numeradas (no admiten igualdad exacta): "Actividad Economica Secundaria 1", "... 2", ...
_SECONDARY_CAPTION = re.compile(r"^actividad economica secundaria(?: (\d{1,2}))?$")
# Contenido minimo para considerar la Ficha CARGADA (la pagina tarda en pintar las filas): estado + tipo + denominacion.
_NAME_KEYS = frozenset({"razon_social_caption", "ruc_y_razon_social"})
REQUIRED_CONTENT_KEYS = frozenset({"estado_contribuyente", "tipo_contribuyente"})


def caption_key(normalized: str) -> str | None:
    key = CAPTIONS.get(normalized)
    if key is not None:
        return key
    match = _SECONDARY_CAPTION.match(normalized)
    return f"actividad_secundaria_{match.group(1) or 0}" if match else None


def is_known_caption(normalized: str) -> bool:
    return caption_key(normalized) is not None


def content_ready(extraction: "FichaExtraction") -> bool:
    """La Ficha esta CARGADA solo si ya hay estado, tipo y denominacion (un documento parcial/menu/contenedor vacio no cuenta)."""
    found = set(extraction.captions_found)
    return REQUIRED_CONTENT_KEYS <= found and bool(_NAME_KEYS & found)


# ENCABEZADO OFICIAL de la Ficha (evidencia de la sonda saneada del intento real): la UNICA celda de la Ficha con un numero de 11 digitos
# es la celda `td.T1` de la tabla 0, fila 0, celda 0, cuyo texto empieza por "FICHA RUC:" seguido del RUC (y, en otra linea, la denominacion).
# No existe una fila "Numero de RUC" ni el formato "RUC - denominacion". El RUC solo se acepta de esa celda, identificada por su prefijo
# de rotulo Y su posicion estructural, y debe ser UNICA; cualquier otra cosa (varios candidatos, otra posicion, un numero incidental
# en un enlace, menu o sesion) no identifica al contribuyente (falla cerrado).
HEADER_POSITION = (0, 0, 0)  # (indice de tabla, fila, celda) observados en vivo


def identify_ruc_from_header(extraction: "FichaExtraction", candidates: list[dict]) -> None:
    """`candidates`: celdas cuyo texto empieza por "FICHA RUC" + 11 digitos, con su posicion {ruc, table, row, cell}. Solo el RUC del
    encabezado oficial (unico, en `HEADER_POSITION`) se acepta. Si ademas hubiera una fila de rotulo con otro RUC, hay conflicto: no se identifica."""
    valid = [c for c in candidates or [] if _RUC_RE.match(str(c.get("ruc", "")))]
    if len(valid) != 1:
        return  # ausente o ambiguo (duplicado)
    only = valid[0]
    if (int(only.get("table", -1)), int(only.get("row", -1)), int(only.get("cell", -1))) != HEADER_POSITION:
        return  # no es el encabezado oficial del documento
    existing = extraction.fields.get("ruc")
    if existing is not None and existing != only["ruc"]:
        extraction.fields.pop("ruc", None)
        extraction.malformed.append("ruc_conflict")
        extraction.ruc_source = None
        return
    extraction.fields["ruc"] = only["ruc"]
    extraction.ruc_source = "OFFICIAL_HEADER"


_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_RUC_RE = re.compile(r"^\d{11}$")
_NONE_TEXT = {"-", "--", "---", "NINGUNO", "NINGUNA", "NO TIENE", "N/A"}
_ACTIVITY_NO_KIND_RE = re.compile(r"^(?:(?:Principal|Secundaria\s*\d*)\s*-\s*)?(?:(\d{4,6})\s*-\s*)?(.+)$", re.I)
_ACTIVITY_RE = re.compile(r"^(Principal|Secundaria\s*\d*)\s*-\s*(?:(\d{4,6})\s*-\s*)?(.+)$", re.I)


@dataclass
class FichaExtraction:
    fields: dict = field(default_factory=dict)
    captions_found: list[str] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    ruc_source: str | None = None  # "CAPTION_ROW" | "OFFICIAL_HEADER" (nunca un valor)


def _clean(lines: list[str]) -> list[str]:
    out = []
    for raw in lines or []:
        text = re.sub(r"\s+", " ", raw or "").strip()[:MAX_LINE_LENGTH]
        text = re.sub(r"^:\s*", "", text)  # la 2.a celda real de cada fila es el separador ":" (observado en vivo)
        if text:
            out.append(text)
    return out


def _scalar(lines: list[str]) -> str | None:
    text = " ".join(_clean(lines)).strip()
    if not text or text.upper() in _NONE_TEXT:
        return None
    return text


def _date(lines: list[str]) -> tuple[date | None, bool]:
    """(valor, malformado). Vacio / '-' => (None, False); formato distinto de dd/mm/aaaa => (None, True)."""
    text = _scalar(lines)
    if text is None:
        return None, False
    match = _DATE_RE.match(text)
    if not match:
        return None, True
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1))), False
    except ValueError:
        return None, True


def _items(lines: list[str]) -> list[str]:
    return [x for x in _clean(lines) if x.upper() not in _NONE_TEXT]


def parse_ficha(
    rows: list[tuple[str, list[str]]],
    header_candidates: list[dict] | None = None,
    grids: list[dict] | None = None,
    titles: list[dict] | None = None,
) -> FichaExtraction:
    result = FichaExtraction()
    seen: set[str] = set()
    extra_activities: list[dict] = []
    domicilio: dict = {}
    for caption, lines in rows[:MAX_ROWS]:
        key = caption_key(normalize_caption(caption))
        if key is None or key in seen:  # rotulo desconocido o repetido: se ignora (nunca se adivina)
            continue
        seen.add(key)
        result.captions_found.append(key)
        fields = result.fields

        if key == "ruc_y_razon_social":
            text = _scalar(lines) or ""
            match = re.match(r"^(\d{11})\s*-\s*(.+)$", text)
            if match and _RUC_RE.match(match.group(1)):
                fields["ruc"] = match.group(1)
                fields["razon_social"] = match.group(2).strip()[:255]
            elif _RUC_RE.match(text):
                fields["ruc"] = text
            else:
                result.malformed.append(key)
        elif key in {"fecha_inscripcion", "fecha_inicio_actividades", "emisor_electronico_desde", "afiliado_ple_desde"}:
            value, bad = _date(lines)
            if bad:
                result.malformed.append(key)
            elif value is not None:
                fields[key] = value.isoformat()
        elif key == "razon_social_caption":
            text = _scalar(lines) or ""
            match = re.match(r"^(\d{11})\s*-\s*(.+)$", text)
            if match:
                fields.setdefault("ruc", match.group(1))
                fields.setdefault("razon_social", match.group(2).strip()[:255])
            elif text and not _RUC_RE.match(text):
                fields.setdefault("razon_social", text[:255])
            elif _RUC_RE.match(text):
                fields.setdefault("ruc", text)
        elif key == "actividad_principal" or key == "actividades_secundarias" or key.startswith("actividad_secundaria_"):
            kind = "PRINCIPAL" if key == "actividad_principal" else "SECUNDARIA"
            for line in _items(lines):
                m = _ACTIVITY_NO_KIND_RE.match(line)
                if not m:
                    result.malformed.append(key)
                    continue
                extra_activities.append({"tipo": kind, "ciiu": m.group(1), "descripcion": m.group(2).strip()[:255]})
        elif key == "actividades_economicas":
            for line in _items(lines):
                m = _ACTIVITY_RE.match(line)
                if not m:
                    result.malformed.append(key)
                    continue
                kind = "PRINCIPAL" if m.group(1).lower().startswith("principal") else "SECUNDARIA"
                extra_activities.append({"tipo": kind, "ciiu": m.group(2), "descripcion": m.group(3).strip()[:255]})
        elif key in _DOMICILIO_KEYS:
            value = _scalar(lines)
            if value is not None:
                domicilio[_DOMICILIO_KEYS[key]] = value[:500 if key == "dom_referencias" else 120]
        elif key in {"comprobantes_pago", "sistema_emision_electronica", "padrones"}:
            items = _items(lines)
            if items:
                fields[key] = items[:30]
        else:
            value = _scalar(lines)
            if value is not None:
                fields[key] = value[:500 if key == "domicilio_fiscal" else 255]
    if domicilio:
        result.fields["domicilio"] = domicilio
    parse_sections(result, list(grids or []), list(titles or []))
    if "ruc" in result.fields and result.ruc_source is None:
        result.ruc_source = "CAPTION_ROW"
    identify_ruc_from_header(result, list(header_candidates or []))
    if extra_activities:
        # la actividad principal primero; el orden relativo de las demas se conserva
        ordered = [a for a in extra_activities if a["tipo"] == "PRINCIPAL"] + [
            a for a in extra_activities if a["tipo"] != "PRINCIPAL"
        ]
        result.fields["actividades_economicas"] = ordered[:20]
    return result



# ---------------------------------------------------------------------------------------------------------------------------------
# Secciones TABULARES de la Ficha (tributos afectos, representantes legales, establecimientos anexos). La extraccion es TOLERANTE y FALLA
# CERRADO: una tabla se interpreta solo si su titulo de seccion y sus encabezados se reconocen; una seccion presente pero no interpretable
# se informa en `secciones_no_reconocidas` (nunca se adivinan columnas). Del representante solo se conserva lo MINIMO (nombre, documento,
# cargo, fecha desde): la fecha de nacimiento, direccion, telefono y correo se descartan en el propio agente.
# ---------------------------------------------------------------------------------------------------------------------------------
SECTION_TITLES: dict[str, str] = {
    "registro de tributos afectos": "tributos_afectos",
    "representantes legales": "representantes_legales",
    "establecimientos anexos": "establecimientos_anexos",
}


def _fold(text: str) -> str:
    return normalize_caption(text)


def _cell_lines(cell: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in (cell or "").split("\n") if line.strip()]


def _cell_text(cell: str) -> str | None:
    text = " ".join(_cell_lines(cell)).strip()
    return None if not text or text.upper() in _NONE_TEXT else text


def _cell_date(cell: str) -> tuple[date | None, bool]:
    return _date([cell or ""])


def _header_index(headers: list[str], *needles: str) -> int | None:
    for index, header in enumerate(headers):
        if all(n in header for n in needles):
            return index
    return None


def _split_grids(grids: list[dict]) -> dict[int, list[list[str]]]:
    by_table: dict[int, list[list[str]]] = {}
    for item in grids:
        try:
            table = int(item["table"])
            cells = [str(c) for c in item["cells"]]
        except (KeyError, TypeError, ValueError):
            continue
        by_table.setdefault(table, []).append(cells)
    return by_table


def _section_of(table: int, titles: list[dict]) -> str | None:
    """Seccion de una tabla: el titulo conocido mas cercano ANTERIOR (o igual) en el documento."""
    best: tuple[int, str] | None = None
    for item in titles:
        try:
            idx, name = int(item["table"]), SECTION_TITLES.get(str(item["title"]))
        except (KeyError, TypeError, ValueError):
            continue
        if name and idx <= table and (best is None or idx > best[0]):
            best = (idx, name)
    return best[1] if best else None


_HEADER_WORDS = ("tributo", "afecto", "apellidos", "cargo", "documento", "codigo", "tipo", "denominacion", "domicilio", "direccion", "ubigeo", "condicion", "desde")


def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """(encabezados normalizados, filas de datos): el encabezado es la PRIMERA fila con al menos dos palabras de rotulo reconocidas; las
    filas anteriores se descartan y, si no hay encabezado, todas las filas son datos (sin columnas adivinadas)."""
    for index, row in enumerate(rows):
        folded = [_fold(c) for c in row]
        if sum(1 for c in folded if any(w in c for w in _HEADER_WORDS)) >= 2 and not any(_DATE_RE.match(c.strip()) for c in row):
            return folded, rows[index + 1:]
    return [], rows


def parse_sections(result: FichaExtraction, grids: list[dict], titles: list[dict]) -> None:
    by_table = _split_grids(grids)
    present = {name for name in (_section_of(t, titles) for t in by_table) if name}
    title_sections = {SECTION_TITLES.get(str(i.get("title"))) for i in titles} - {None}
    tributos: list[dict] = []
    representantes: list[dict] = []
    establecimientos: list[dict] = []
    for table in sorted(by_table):
        section = _section_of(table, titles)
        rows = by_table[table]
        headers, data = _split_header(rows)
        if section == "tributos_afectos" or (section is None and _header_index(headers, "tributo") is not None and _header_index(headers, "afecto") is not None):
            tributos.extend(_parse_tributos(headers, data, result))
        elif section == "representantes_legales":
            representantes.extend(_parse_representantes(headers, data))
        elif section == "establecimientos_anexos":
            establecimientos.extend(_parse_establecimientos(headers, data))
    # los tributos se admiten sin titulo reconocido solo por su encabezado; las demas secciones exigen titulo
    if tributos:
        result.fields["tributos_afectos"] = tributos[:40]
    if representantes:
        result.fields["representantes_legales"] = representantes[:20]
    if establecimientos:
        result.fields["establecimientos_anexos"] = establecimientos[:60]
    missing = sorted(s for s in title_sections if s and s not in result.fields)
    # una seccion cuyo titulo aparece pero no se pudo interpretar se informa (puede ser legitimamente vacia: sin filas de datos)
    unrecognised = [s for s in missing if any(_section_of(t, titles) == s for t in by_table)]
    if unrecognised:
        result.fields["secciones_no_reconocidas"] = unrecognised[:10]


def _parse_tributos(headers: list[str], data: list[list[str]], result: FichaExtraction) -> list[dict]:
    """Filas de tributos: nombre (primera celda con texto que no sea ':'), fecha 'afecto desde' (primera celda con fecha o '-') y, solo
    con 5 columnas, marca de exoneracion y su rango. Sin depender del encabezado; las filas duplicadas (diseno de escritorio y movil) se
    unifican."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for cells in data:
        useful = [c for c in cells if _cell_text(c) not in (None, ":")]
        if len(useful) < 1:
            continue
        name = _cell_text(useful[0])
        if not name or _fold(name) in {"tributo", "tributos", "afecto desde", "marca de exoneracion"} or _DATE_RE.match(name):
            continue
        since_cell = next((c for c in useful[1:] if _DATE_RE.match((_cell_text(c) or "").strip())), None)
        if since_cell is None and len(useful) > 1 and _cell_text(useful[1]) not in (None,) and (_cell_text(useful[1]) or "").strip() not in _NONE_TEXT:
            continue  # una fila con una segunda celda que no es fecha ni '-' no es una fila de tributo
        since, bad = _cell_date(since_cell) if since_cell is not None else (None, False)
        if bad:
            result.malformed.append("tributo_fecha")
            continue
        row: dict = {"tributo": name[:255]}
        if since:
            row["afecto_desde"] = since.isoformat()
        if len(cells) >= 5:
            mark = _cell_text(cells[2])
            if mark:
                row["marca_exoneracion"] = mark[:120]
            for index, key in ((3, "exoneracion_desde"), (4, "exoneracion_hasta")):
                value, bad2 = _cell_date(cells[index])
                if bad2:
                    result.malformed.append("tributo_exoneracion")
                elif value:
                    row[key] = value.isoformat()
        signature = (row["tributo"], row.get("afecto_desde"))
        if signature in seen:
            continue
        seen.add(signature)
        out.append(row)
    return out


def _parse_representantes(headers: list[str], data: list[list[str]]) -> list[dict]:
    name_i = _header_index(headers, "apellidos")
    if name_i is None:
        return []  # encabezados no reconocidos: no se adivinan columnas
    doc_i = _header_index(headers, "documento")
    cargo_i = _header_index(headers, "cargo")
    since_i = _header_index(headers, "desde")
    out = []
    for cells in data:
        if len(cells) <= name_i:
            continue
        name = _cell_text(cells[name_i])
        if not name:
            continue
        row: dict = {"nombre": name[:255]}
        if doc_i is not None and doc_i < len(cells):
            lines = _cell_lines(cells[doc_i])
            digits = [line for line in lines if re.fullmatch(r"[0-9A-Za-z-]{4,20}", line) and any(ch.isdigit() for ch in line)]
            if digits:
                row["numero_documento"] = digits[-1]
            words = [line for line in lines if line not in digits]
            if words:
                row["tipo_documento"] = " ".join(words)[:120]
        if cargo_i is not None and cargo_i < len(cells) and _cell_text(cells[cargo_i]):
            row["cargo"] = _cell_text(cells[cargo_i])[:120]
        if since_i is not None and since_i < len(cells):
            value, bad = _cell_date(cells[since_i])
            if value and not bad:
                row["fecha_desde"] = value.isoformat()
        out.append(row)
    return out


def _parse_establecimientos(headers: list[str], data: list[list[str]]) -> list[dict]:
    code_i = _header_index(headers, "codigo")
    if code_i is None:
        return []
    mapping = {
        "tipo": _header_index(headers, "tipo"), "denominacion": _header_index(headers, "denominacion"),
        "direccion": _header_index(headers, "domicilio") if _header_index(headers, "domicilio") is not None else _header_index(headers, "direccion"),
        "ubicacion": _header_index(headers, "ubigeo"), "condicion_legal": _header_index(headers, "condicion"),
    }
    out = []
    for cells in data:
        code = _cell_text(cells[code_i]) if code_i < len(cells) else None
        if not code:
            continue
        row: dict = {"codigo": code[:12]}
        for key, index in mapping.items():
            if index is not None and index < len(cells):
                value = _cell_text(cells[index])
                if value:
                    row[key] = value[:500 if key == "direccion" else 255]
        out.append(row)
    return out
