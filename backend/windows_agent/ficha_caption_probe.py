"""M2 -- sonda de ROTULOS de la Ficha RUC (solo desarrollo, apagada por defecto). Registra, para validar en vivo el
contrato campo-rotulo-valor, UNICAMENTE rotulos genericos y la forma estructural de cada fila; jamas valores.

Garantias (todas probadas offline):
* solo se activa si el operador define `SOL_AGENT_FICHA_PROBE_DIR` (el agente de produccion nunca la ejecuta);
* un rotulo solo sale si TODAS sus palabras pertenecen a un vocabulario cerrado de rotulos tributarios (`VOCABULARY`);
  cualquier otro texto de la primera celda (razon social, domicilio, numeros...) se sustituye por una marca
  `redacted` con solo su longitud y numero de palabras;
* de los valores solo salen la cantidad de lineas y su CLASE (`date`/`ruc`/`activity`/`text`/`empty`), no su texto;
* sin HTML, DOM, cookies, URLs, capturas ni trazas; el archivo se escribe atomicamente y UTF-8 sin BOM, en una carpeta
  elegida por el operador (fuera del repositorio)."""

from __future__ import annotations

import json
import os
import pathlib
import re
import unicodedata

PROBE_VERSION = 1
PROBE_ENV = "SOL_AGENT_FICHA_PROBE_DIR"
MAX_LABEL_LENGTH = 90
MAX_ROWS = 300

# Vocabulario CERRADO de palabras de rotulo (normalizadas, sin acentos). Un nombre propio o un valor tributario no
# queda cubierto por completo, de modo que nunca pasa el filtro.
VOCABULARY = frozenset(
    """
    numero de ruc tipo contribuyente nombre comercial fecha inscripcion inicio actividades estado condicion domicilio
    fiscal sistema emision comprobante comprobantes pago electronico electronicos electronica contabilidad comercio
    exterior actividad economica economicas principal secundaria secundarias padrones afiliado ple desde emisor apellidos
    y o razon social nombres del la el los las en con c aut impresion impresiones f u 806 816 es s tipos situacion
    contribuyentes persona natural juridica ficha datos identificacion generales informacion otros registros
    representante representantes legal legales tributaria tributarias obligaciones afectaciones regimen regimenes
    declaracion declaraciones libros llevado autorizacion autorizaciones local locales anexo anexos establecimiento
    establecimientos tributo tributos de a al por para
    dependencia sunat departamento provincia distrito zona nombre via nro km mz lote dpto interior otras referencias inmueble declarado
    afecto marca exoneracion hasta cargo documento documentos nacimiento representacion orden codigo denominacion ubigeo licencia
    telefono correo direccion vinculadas personas vinculo origen porcentaje pais constitucion residencia entidad registral partida
    """.split()
)

_DATE_LINE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_RUC_LINE = re.compile(r"^\d{11}(\s*-\s*.+)?$")
_ACTIVITY_LINE = re.compile(r"^(principal|secundaria\s*\d*)?\s*-?\s*\d{4,6}\s*-\s*.+$", re.I)

# Una sola funcion en la pagina: devuelve etiquetas CRUDAS de la primera celda y la forma de las demas; el filtro por
# vocabulario se aplica en Python, antes de escribir nada, y ningun valor sale del proceso del agente.
CENSUS_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [];
  const tables = Array.from(document.querySelectorAll('table')).filter(visible);
  tables.forEach((table, ti) => {
    table.querySelectorAll('tr').forEach((tr, ri) => {
      if (out.length >= 300) return;
      const cells = Array.from(tr.children).filter(c => c.tagName === 'TD' || c.tagName === 'TH');
      if (cells.length < 2) return;
      const label = (cells[0].innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200);
      const lines = [];
      for (const c of cells.slice(1)) for (const l of (c.innerText || '').split('\n')) { const t = l.trim(); if (t) lines.push(t.slice(0, 200)); }
      out.push({ label, cells: cells.length, table: ti, row: ri, tag: cells[0].tagName, lines });
    });
  });
  // Donde aparece un numero de 11 digitos (SOLO coordenadas estructurales: etiqueta, clases, indices; jamas el texto ni los digitos).
  const rucLike = [];
  for (const el of document.querySelectorAll('td,th,h1,h2,h3,h4,div,span,b,strong,p,a,li')) {
    if (rucLike.length >= 12) break;
    if (el.children.length > 0 && el.tagName !== 'TD' && el.tagName !== 'TH') continue;
    const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!/\d{11}/.test(txt)) continue;
    const cell = el.closest('td,th'); const tr = el.closest('tr'); const table = el.closest('table');
    rucLike.push({
      tag: el.tagName, classes: Array.from(el.classList).slice(0, 4), role: el.getAttribute('role'),
      starts11: /^\d{11}/.test(txt), dash_after: /^\d{11} ?- ?\S/.test(txt), len: txt.length, words: txt.split(' ').length,
      cell: cell && tr ? Array.from(tr.children).indexOf(cell) : -1, row: tr ? Array.from(tr.parentElement.children).indexOf(tr) : -1,
      table: table ? tables.indexOf(table) : -1,
    });
  }
  // Encabezado oficial "FICHA RUC: <11 digitos>": SOLO posicion y estructura (nunca los digitos ni el texto).
  const headers = [];
  const allTables = Array.from(document.querySelectorAll('table'));
  for (const c of document.querySelectorAll('td,th')) {
    if (headers.length >= 8) break;
    const m = (c.innerText || '').replace(/\s+/g, ' ').trim().match(/^ficha ruc ?:? ?(\d{11})(?!\d)/i);
    if (!m) continue;
    const tr = c.parentElement; const table = c.closest('table');
    headers.push({ tag: c.tagName, classes: Array.from(c.classList).slice(0, 4), table: table ? allTables.indexOf(table) : -1,
      row: tr ? Array.from(tr.parentElement.children).indexOf(tr) : -1, cell: tr ? Array.from(tr.children).indexOf(c) : -1,
      nested_tags: Array.from(c.querySelectorAll('*')).slice(0, 6).map(e => e.tagName), lines: (c.innerText || '').split('\n').filter(l => l.trim()).length });
  }
  // Encabezados de las tablas TABULARES (tributos, representantes, establecimientos): solo el texto de cada celda de la primera fila ancha,
  // que se filtra por vocabulario cerrado en Python antes de salir (cualquier valor queda redactado).
  const gridHeaders = [];
  tables.forEach((table, ti) => {
    if (gridHeaders.length >= 12) return;
    const trs = Array.from(table.querySelectorAll('tr'));
    const wide = trs.find(tr => Array.from(tr.children).filter(c => c.tagName === 'TD' || c.tagName === 'TH').length >= 4);
    if (!wide) return;
    gridHeaders.push({ table: ti, rows: trs.length, cells: Array.from(wide.children).slice(0, 14).map(c => (c.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80)) });
  });
  return { tables: tables.length, rows: out, rucLike, headers, gridHeaders };
}
"""


def _norm(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip().lower().rstrip(":").strip()


def is_generic_label(text: str) -> bool:
    norm = _norm(text)
    if not norm or len(norm) > MAX_LABEL_LENGTH:
        return False
    words = [w for w in re.split(r"[^a-z0-9]+", norm) if w]
    return bool(words) and all(w in VOCABULARY for w in words)


def classify_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return "empty"
    if _DATE_LINE.match(text):
        return "date"
    if _RUC_LINE.match(text):
        return "ruc"
    if _ACTIVITY_LINE.match(text):
        return "activity"
    return "text"


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_ELEVEN_DIGITS = re.compile(r"\d{11}")


def label_shape(label: str) -> dict:
    """Forma (no contenido) de una primera celda que NO es un rotulo del vocabulario: si empieza por 11 digitos, si los contiene, si hay
    guion, y las palabras del vocabulario con que EMPIEZA (p. ej. 'numero de ruc'); nunca los digitos ni el resto del texto."""
    norm = _norm(label)
    prefix: list[str] = []
    for word in [w for w in re.split(r"[^a-z0-9]+", norm) if w]:
        if word in VOCABULARY and not word.isdigit():
            prefix.append(word)
        else:
            break
    return {
        "starts_with_11_digits": bool(re.match(r"^\d{11}", norm)),
        "contains_11_digits": bool(_ELEVEN_DIGITS.search(norm)),
        "has_dash": "-" in norm,
        "leading_vocab_words": prefix[:6],
    }


def sanitize_headers(items: list[dict]) -> dict:
    """Candidatos al encabezado oficial "FICHA RUC": cuantos hay y su posicion/estructura; jamas los digitos."""
    out = []
    for raw in (items or [])[:8]:
        classes = [c for c in (raw.get("classes") or [])[:4] if isinstance(c, str) and _SAFE_TOKEN.match(c)]
        out.append({
            "tag": str(raw.get("tag", ""))[:4] if re.fullmatch(r"[A-Za-z0-9]{1,4}", str(raw.get("tag", ""))) else "?",
            "classes": classes,
            "table_index": int(raw.get("table", -1)), "row_index": int(raw.get("row", -1)), "cell_index": int(raw.get("cell", -1)),
            "nested_tags": [x for x in (raw.get("nested_tags") or [])[:6] if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9]{1,8}", x)],
            "text_lines": int(raw.get("lines", 0)),
        })
    return {"candidates": len(out), "at_expected_position": len(out) == 1 and (out[0]["table_index"], out[0]["row_index"], out[0]["cell_index"]) == (0, 0, 0), "items": out}


def sanitize_ruc_like(items: list[dict]) -> list[dict]:
    out = []
    for raw in (items or [])[:12]:
        classes = [c for c in (raw.get("classes") or [])[:4] if isinstance(c, str) and _SAFE_TOKEN.match(c)]
        role = raw.get("role")
        out.append({
            "tag": str(raw.get("tag", ""))[:8] if re.fullmatch(r"[A-Za-z0-9]{1,8}", str(raw.get("tag", ""))) else "?",
            "classes": classes,
            "role": role if isinstance(role, str) and _SAFE_TOKEN.match(role) else None,
            "starts_with_11_digits": bool(raw.get("starts11")),
            "dash_after_11_digits": bool(raw.get("dash_after")),
            "text_length": int(raw.get("len", 0)),
            "words": int(raw.get("words", 0)),
            "cell_index": int(raw.get("cell", -1)),
            "row_index": int(raw.get("row", -1)),
            "table_index": int(raw.get("table", -1)),
        })
    return out


def sanitize_grid_headers(items: list[dict]) -> list[dict]:
    """Encabezados de tablas tabulares: cada celda solo sale si TODAS sus palabras estan en el vocabulario cerrado; si no, solo su longitud."""
    out = []
    for raw in (items or [])[:12]:
        cells = []
        for text in (raw.get("cells") or [])[:14]:
            text = str(text)
            cells.append({"label": _norm(text)} if is_generic_label(text) else {"label": None, "redacted": True, "length": len(text)})
        out.append({"table": int(raw.get("table", -1)), "rows": int(raw.get("rows", 0)), "cells": cells})
    return out


def build_probe(census: dict, parsed_summary: dict | None = None) -> dict:
    """Convierte el censo crudo (que no sale del proceso) en el informe sanitizado."""
    rows = []
    redacted = 0
    for raw in (census.get("rows") or [])[:MAX_ROWS]:
        label = str(raw.get("label", ""))
        lines = [str(x) for x in raw.get("lines", [])]
        shape = {
            "cells": int(raw.get("cells", 0)),
            "table": int(raw.get("table", 0)),
            "row": int(raw.get("row", 0)),
            "label_tag": str(raw.get("tag", ""))[:3],
            "value_lines": len(lines),
            "value_kinds": [classify_line(x) for x in lines[:20]],
        }
        if is_generic_label(label):
            rows.append({"label": _norm(label), **shape})
        else:
            redacted += 1
            rows.append({"label": None, "redacted": True, "label_length": len(label), "label_words": len(label.split()), **shape, **label_shape(label)})
    return {
        "probe_version": PROBE_VERSION,
        "tables_visible": int(census.get("tables", 0)),
        "eleven_digit_cells": sanitize_ruc_like(census.get("rucLike") or []),
        "ficha_header": sanitize_headers(census.get("headers") or []),
        "rows": rows,
        "grid_headers": sanitize_grid_headers(census.get("gridHeaders") or []),
        "redacted_rows": redacted,
        "parse": parsed_summary or {},
    }


def write_probe(directory: str | os.PathLike, report: dict) -> pathlib.Path:
    target_dir = pathlib.Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / "ficha_caption_probe.json"
    temp = target_dir / "ficha_caption_probe.json.tmp"
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, final)
    return final
