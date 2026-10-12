"""M2: sonda de rotulos de la Ficha RUC (solo desarrollo) y variantes candidatas del parser. Datos FICTICIOS."""

from __future__ import annotations

import ast
import json
import pathlib

from windows_agent import ficha_caption_probe as probe
from windows_agent import ficha_parser as parser

SENSITIVE = ["EMPRESA FICTICIA ZETA S.A.C.", "20123456786", "AV. LOS OLIVOS 123 - LIMA", "01/02/2015", "6201 - PROGRAMACION"]


def _row(label, lines, table=0, row=0):
    return {"label": label, "cells": 2, "table": table, "row": row, "tag": "TD", "lines": lines}


def _census():
    return {
        "tables": 3,
        "rows": [
            _row("Apellidos y Nombres o Razón Social:", ["20123456786 - EMPRESA FICTICIA ZETA S.A.C."]),
            _row("Tipo de Contribuyente:", ["SOCIEDAD ANONIMA CERRADA"]),
            _row("Fecha de Inscripción:", ["01/02/2015"]),
            _row("Domicilio Fiscal:", ["AV. LOS OLIVOS 123 - LIMA"]),
            _row("Actividad Económica Principal:", ["6201 - PROGRAMACION"]),
            # primera celda con un VALOR (no un rotulo): nunca debe salir su texto
            _row("EMPRESA FICTICIA ZETA S.A.C.", ["x"]),
            _row("20123456786", []),
            _row("AV. LOS OLIVOS 123", ["-"]),
        ],
    }


def test_only_closed_vocabulary_labels_leave_the_agent_and_values_never_do(tmp_path):
    report = probe.build_probe(_census(), {"captions_found": ["tipo_contribuyente"]})
    text = json.dumps(report, ensure_ascii=False)
    for secret in SENSITIVE:
        assert secret not in text
    labels = [r["label"] for r in report["rows"] if r.get("label")]
    assert "apellidos y nombres o razon social" in labels and "domicilio fiscal" in labels
    assert report["redacted_rows"] == 3 and report["tables_visible"] == 3
    redacted = [r for r in report["rows"] if r.get("redacted")]
    assert all(set(r) >= {"label_length", "label_words"} and r["label"] is None for r in redacted)
    kinds = {r["label"]: r["value_kinds"] for r in report["rows"] if r.get("label")}
    assert kinds["fecha de inscripcion"] == ["date"] and kinds["apellidos y nombres o razon social"] == ["ruc"]
    assert kinds["actividad economica principal"] == ["activity"] and kinds["tipo de contribuyente"] == ["text"]
    # el archivo es atomico, UTF-8 sin BOM, y no contiene nada de lo anterior
    written = probe.write_probe(tmp_path / "salida", report)
    raw = written.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and not list(written.parent.glob("*.tmp"))
    assert all(secret not in raw.decode("utf-8") for secret in SENSITIVE)


def test_label_filter_rejects_values_digits_and_long_text():
    assert probe.is_generic_label("Condición de Domicilio Fiscal:")
    assert probe.is_generic_label("Comprobantes de Pago c/aut. de Impresión (F. 806 u 816)")
    for value in ["EMPRESA FICTICIA ZETA S.A.C.", "20123456786", "", "A" * 200, "Domicilio Fiscal: AV. 1", "Estado 2024"]:
        assert not probe.is_generic_label(value)


def test_the_census_script_reads_text_only_and_never_markup_or_storage():
    js = probe.CENSUS_JS
    for forbidden in ("innerHTML", "outerHTML", "textContent", "cookie", "localStorage", "sessionStorage", "fetch(", "click("):
        assert forbidden not in js
    tree = ast.parse(pathlib.Path(probe.__file__).read_text(encoding="utf-8"))
    imported = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    }
    assert not {m.split(".")[0] for m in imported if m} & {"playwright", "httpx", "requests", "socket", "logging"}


def test_probe_is_off_unless_the_operator_opts_in(monkeypatch):
    monkeypatch.delenv(probe.PROBE_ENV, raising=False)
    from windows_agent.destination_executor import DestinationNavigator

    class Boom:
        def __getattr__(self, name):  # cualquier acceso a la pagina o al frame es un fallo
            raise AssertionError("la sonda no debe tocar el navegador sin opt-in")

    nav = DestinationNavigator.__new__(DestinationNavigator)
    assert nav.probe_ficha_captions(Boom()) is None


def test_candidate_caption_variants_map_onto_the_v1_schema_without_inventing_values():
    rows = [
        ("Apellidos y Nombres o Razón Social:", ["20123456786 - EMPRESA FICTICIA ZETA S.A.C."]),
        ("Tipo de Contribuyente:", ["SOCIEDAD ANONIMA CERRADA"]),
        ("Condición de Domicilio Fiscal:", ["HABIDO"]),
        ("Actividades Económicas Secundarias:", ["7020 - CONSULTORIA", "8299 - OTRAS"]),
        ("Actividad Económica Principal:", ["6201 - PROGRAMACION"]),
        ("Sistema Emisión Comprobantes de Pago:", ["MANUAL"]),
    ]
    result = parser.parse_ficha(rows)
    f = result.fields
    assert f["ruc"] == "20123456786" and f["razon_social"] == "EMPRESA FICTICIA ZETA S.A.C."
    assert f["tipo_contribuyente"] == "SOCIEDAD ANONIMA CERRADA" and f["condicion_contribuyente"] == "HABIDO"
    assert f["sistema_emision_comprobante"] == "MANUAL"
    assert [a["tipo"] for a in f["actividades_economicas"]] == ["PRINCIPAL", "SECUNDARIA", "SECUNDARIA"]
    assert f["actividades_economicas"][0]["ciiu"] == "6201" and result.malformed == []
    assert parser.CAPTIONS_LIVE_VALIDATED is False  # sigue sin declararse validado hasta confirmarlo en vivo


def test_plain_name_under_the_social_reason_caption_does_not_fabricate_a_ruc():
    result = parser.parse_ficha([("Apellidos y Nombres o Razón Social", ["EMPRESA SOLA S.A."])])
    assert result.fields == {"razon_social": "EMPRESA SOLA S.A."} and "ruc" not in result.fields
