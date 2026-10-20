"""M4: importacion CSV/XLSX controlada (fixtures SINTETICOS; no se asume ningun formato oficial de SUNAT ni de DARWIN)."""

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.services import sire_import_parser as P

D = Decimal


def csv_bytes(text: str, encoding="utf-8") -> io.BytesIO:
    return io.BytesIO(text.encode(encoding))


def xlsx_stream(rows, *, sheets=None, formula_cell=None) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = (sheets or ["Datos"])[0]
    for row in rows:
        ws.append(row)
    for extra in (sheets or [])[1:]:
        wb.create_sheet(extra).append(["x"])
    if formula_cell:
        ws[formula_cell] = "=1+1"
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def agg(table, mapping, **kwargs):
    kwargs.setdefault("mode", "DETAIL")
    kwargs.setdefault("decimal_style", "DOT")
    kwargs.setdefault("component", "RVIE")
    kwargs.setdefault("period_year", 2026)
    kwargs.setdefault("period_month", 9)
    return P.aggregate(table, mapping, **kwargs)


def test_csv_detects_encoding_and_delimiter_and_previews():
    for delimiter in (",", ";", "\t", "|"):
        table = P.read_csv(csv_bytes(f"Base{delimiter}IGV{delimiter}Total\n100.00{delimiter}18.00{delimiter}118.00\n"), max_rows=10)
        assert table.delimiter == delimiter and table.headers == ["Base", "IGV", "Total"] and len(table.rows) == 1
    latin = P.read_csv(csv_bytes("Descripción;Total\nañejo;10,50\n", "cp1252"), max_rows=10)
    assert latin.encoding == "cp1252" and latin.headers[0] == "Descripción"
    bom = P.read_csv(csv_bytes("﻿a,b\n1,2\n"), max_rows=10)
    assert bom.headers == ["a", "b"]


def test_csv_rejects_binary_empty_and_inconsistent_content():
    with pytest.raises(P.ImportRejected) as e1:
        P.read_csv(io.BytesIO(b"a,b\n1,\x00\n"), max_rows=10)
    assert e1.value.code == "IMPORT_BINARY_CONTENT"
    with pytest.raises(P.ImportRejected) as e2:
        P.read_csv(csv_bytes("sin delimitador alguno\notra linea\n"), max_rows=10)
    assert e2.value.code == "IMPORT_DELIMITER_UNDETECTED"
    with pytest.raises(P.ImportRejected) as e3:
        P.read_csv(io.BytesIO(b"\x81\x8d,\x8f\n1,2\n"), max_rows=10)
    assert e3.value.code in {"IMPORT_ENCODING_UNSUPPORTED", "IMPORT_DELIMITER_UNDETECTED"}


def test_row_and_column_limits():
    rows = "a,b\n" + "1,2\n" * 11
    with pytest.raises(P.ImportRejected) as raised:
        P.read_csv(csv_bytes(rows), max_rows=10)
    assert raised.value.code == "IMPORT_TOO_MANY_ROWS"
    wide = ",".join(f"c{i}" for i in range(P.MAX_COLUMNS + 1)) + "\n" + ",".join("1" for _ in range(P.MAX_COLUMNS + 1)) + "\n"
    with pytest.raises(P.ImportRejected) as raised:
        P.read_csv(csv_bytes(wide), max_rows=10)
    assert raised.value.code == "IMPORT_TOO_MANY_COLUMNS"
    preview = P.read_csv(csv_bytes("a,b\n" + "1,2\n" * 100), max_rows=10, preview=True)
    assert len(preview.rows) == P.MAX_PREVIEW_ROWS and preview.truncated


def test_ambiguous_blank_and_duplicate_headers_are_rejected():
    for text, code in (("Total,total\n1,2\n", "IMPORT_AMBIGUOUS_HEADERS"), ("A,,C\n1,2,3\n", "IMPORT_BLANK_HEADER"), ("Tótal,Total\n1,2\n", "IMPORT_AMBIGUOUS_HEADERS")):
        with pytest.raises(P.ImportRejected) as raised:
            P.read_csv(csv_bytes(text), max_rows=10)
        assert raised.value.code == code, text


def test_detail_aggregation_with_exact_decimals_and_no_float():
    table = P.read_csv(csv_bytes("Base Imponible,IGV,Importe Total\n100.10,18.02,118.12\n0.20,0.04,0.24\n"), max_rows=10)
    result = agg(table, {"base_amount": "base imponible", "igv_amount": "IGV", "total_amount": "Importe Total"})
    assert (result.record_count, result.base_amount, result.igv_amount, result.total_amount) == (2, D("100.30"), D("18.06"), D("118.36"))


def test_summary_mode_and_decimal_styles():
    table = P.read_csv(csv_bytes("Cantidad;Total\n1.250;1.234.567,89\n"), max_rows=10)
    comma = agg(table, {"record_count": "Cantidad", "total_amount": "Total"}, mode="SUMMARY", decimal_style="COMMA")
    assert comma.record_count == 1250 and comma.total_amount == D("1234567.89")
    dot = P.read_csv(csv_bytes("Cantidad,Total\n1250,\"1,234,567.89\"\n"), max_rows=10)
    assert agg(dot, {"record_count": "Cantidad", "total_amount": "Total"}, mode="SUMMARY", decimal_style="DOT").total_amount == D("1234567.89")
    with pytest.raises(P.ImportRejected) as raised:  # resumen con 2 filas
        agg(P.read_csv(csv_bytes("Total;Nota\n1;a\n2;b\n"), max_rows=10), {"total_amount": "Total"}, mode="SUMMARY")
    assert raised.value.code == "IMPORT_ROW_ERRORS"


def test_ambiguous_thousands_separator_requires_an_explicit_choice():
    table = P.read_csv(csv_bytes("Total;Nota\n\"1,234\";x\n"), max_rows=10)
    with pytest.raises(P.ImportRejected) as raised:
        agg(table, {"total_amount": "Total"}, decimal_style="AUTO")
    assert raised.value.code == "IMPORT_DECIMAL_AMBIGUOUS"
    assert agg(table, {"total_amount": "Total"}, decimal_style="DOT").total_amount == D("1234")
    with pytest.raises(P.ImportRejected) as comma:  # 1,234 como COMMA = 1.234 -> 3 decimales: nunca se redondea
        agg(table, {"total_amount": "Total"}, decimal_style="COMMA")
    assert comma.value.errors[0]["code"] == "IMPORT_NUMBER_INVALID"
    style, ambiguous = P.detect_decimal_style(table, [0])
    assert style is None and ambiguous
    clear = P.read_csv(csv_bytes("Total;Nota\n\"1,234.56\";x\n"), max_rows=10)
    assert P.detect_decimal_style(clear, [0]) == ("DOT", False)
    assert agg(clear, {"total_amount": "Total"}, decimal_style="AUTO").total_amount == D("1234.56")


def test_invalid_numbers_empty_amounts_and_more_than_two_decimals_block_the_import():
    for cell, code in (("abc", "IMPORT_NUMBER_INVALID"), ("1.234", "IMPORT_NUMBER_INVALID"), ("", "IMPORT_AMOUNT_EMPTY"), ("12.345,6", "IMPORT_NUMBER_INVALID")):
        table = P.read_csv(csv_bytes(f"Total,Nota\n\"{cell}\",x\n"), max_rows=10)
        with pytest.raises(P.ImportRejected) as raised:
            agg(table, {"total_amount": "Total"}, decimal_style="DOT")
        assert raised.value.code in {"IMPORT_ROW_ERRORS", "IMPORT_DECIMAL_AMBIGUOUS"}, cell
        if raised.value.code == "IMPORT_ROW_ERRORS":
            assert raised.value.errors[0]["code"] == code, cell


def test_total_rows_are_rejected_unless_explicitly_ignored():
    table = P.read_csv(csv_bytes("Detalle,Total\nF001-1,10.00\nF001-2,20.00\nTOTAL,30.00\n"), max_rows=10)
    with pytest.raises(P.ImportRejected) as raised:
        agg(table, {"total_amount": "Total"})
    assert raised.value.code == "IMPORT_ROW_ERRORS" and raised.value.errors[0]["code"] == "IMPORT_TOTAL_ROW" and raised.value.errors[0]["row"] == 4
    ok = agg(table, {"total_amount": "Total"}, ignore_total_rows=True)
    assert ok.record_count == 2 and ok.total_amount == D("30.00") and ok.ignored_total_rows == 1


def test_period_and_record_type_validation():
    table = P.read_csv(csv_bytes("Periodo,Tipo,Total\n202609,Ventas,10.00\n2026-09,RVIE,5.00\n"), max_rows=10)
    ok = agg(table, {"total_amount": "Total", "period": "Periodo", "record_type": "Tipo"})
    assert ok.total_amount == D("15.00")
    for csv_text, code in (
        ("Periodo,Total\n202608,10.00\n", "IMPORT_PERIOD_MISMATCH"),
        ("Periodo,Total\nxx,10.00\n", "IMPORT_PERIOD_INVALID"),
        ("Tipo,Total\nCompras,10.00\n", "IMPORT_TYPE_MISMATCH"),
        ("Tipo,Total\nOtro,10.00\n", "IMPORT_TYPE_UNRECOGNIZED"),
    ):
        mapping = {"total_amount": "Total", "period": "Periodo"} if "Periodo" in csv_text else {"total_amount": "Total", "record_type": "Tipo"}
        with pytest.raises(P.ImportRejected) as raised:
            agg(P.read_csv(csv_bytes(csv_text), max_rows=10), mapping)
        assert raised.value.errors[0]["code"] == code, csv_text
    assert P.period_of("15/09/2026") == (2026, 9) and P.period_of("09/2026") == (2026, 9) and P.period_of(date(2026, 9, 3)) == (2026, 9) and P.period_of("2026-13") is None


def test_mapping_validation():
    table = P.read_csv(csv_bytes("A,B\n1,2\n"), max_rows=10)
    for mapping, code in (({"total_amount": "Z"}, "IMPORT_MAPPING_COLUMN_NOT_FOUND"), ({"total_amount": "A", "base_amount": "a"}, "IMPORT_MAPPING_DUPLICATE_COLUMN"),
                          ({"evil": "A"}, "IMPORT_MAPPING_FIELD_UNKNOWN"), ({"record_count": "A"}, "IMPORT_MAPPING_COUNT_IN_DETAIL")):
        with pytest.raises(P.ImportRejected) as raised:
            agg(table, mapping)
        assert raised.value.code == code, mapping


def test_xlsx_reads_values_selects_sheet_and_handles_numbers_without_float_drift():
    stream = xlsx_stream([["Base", "Total"], [100.1, 118.12], [0.2, 0.24]], sheets=["Ventas", "Otra"])
    assert P.list_sheets(stream) == ["Ventas", "Otra"]
    table = P.read_xlsx(stream, max_rows=10, sheet="Ventas")
    assert table.kind == "xlsx" and table.sheet == "Ventas"
    result = agg(table, {"base_amount": "Base", "total_amount": "Total"})
    assert result.base_amount == D("100.30") and result.total_amount == D("118.36")
    with pytest.raises(P.ImportRejected) as raised:
        P.read_xlsx(xlsx_stream([["a"], [1]], sheets=["A", "B"]), max_rows=10)
    assert raised.value.code == "IMPORT_SHEET_REQUIRED"
    with pytest.raises(P.ImportRejected) as raised:
        P.read_xlsx(xlsx_stream([["a"], [1]]), max_rows=10, sheet="No existe")
    assert raised.value.code == "IMPORT_SHEET_NOT_FOUND"


def test_xlsx_formulas_macros_external_links_and_corrupt_files_are_rejected():
    with pytest.raises(P.ImportRejected) as raised:
        P.read_xlsx(xlsx_stream([["Total"], [1]], formula_cell="A2"), max_rows=10)
    assert raised.value.code == "IMPORT_XLSX_FORMULA"
    for marker in ("xl/vbaProject.bin", "xl/externalLinks/externalLink1.xml", "xl/embeddings/oleObject1.bin", "xl/activeX/activeX1.xml"):
        base = xlsx_stream([["Total"], [1]])
        out = io.BytesIO()
        with zipfile.ZipFile(base) as src, zipfile.ZipFile(out, "w") as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr(marker, b"x")
        out.seek(0)
        with pytest.raises(P.ImportRejected) as raised:
            P.read_xlsx(out, max_rows=10)
        assert raised.value.code == "IMPORT_XLSX_ACTIVE_CONTENT", marker
    with pytest.raises(P.ImportRejected) as raised:
        P.read_xlsx(io.BytesIO(b"PK\x03\x04 no es un zip valido"), max_rows=10)
    assert raised.value.code == "IMPORT_XLSX_CORRUPT"


def test_csv_formula_like_cells_are_inert_text_and_fail_as_numbers():
    table = P.read_csv(csv_bytes("Total,Nota\n=1+1,=cmd|' /C calc'!A0\n"), max_rows=10)
    with pytest.raises(P.ImportRejected) as raised:
        agg(table, {"total_amount": "Total"})
    assert raised.value.errors[0]["code"] == "IMPORT_NUMBER_INVALID"  # jamas se evalua: es texto


def test_parser_never_executes_anything_nor_imports_dangerous_modules():
    import inspect

    code = inspect.getsource(P)
    for forbidden in ("eval(", "exec(", "subprocess", "os.system", "pickle", "float("):
        assert forbidden not in code, forbidden
