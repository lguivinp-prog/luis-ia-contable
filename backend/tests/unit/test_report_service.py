import io

from openpyxl import load_workbook

from app.services.report_service import ReportData, render_csv, render_xlsx


def _sample_data() -> ReportData:
    return ReportData(
        report_type="CLIENTS",
        columns=["id", "business_name"],
        rows=[["1", "=cmd|'/c calc'!A1"], ["2", "Empresa normal S.A.C."]],
        truncated=False,
    )


def test_render_csv_neutralizes_formula_injection() -> None:
    csv_text = render_csv(_sample_data())
    lines = csv_text.splitlines()
    assert lines[0] == "id,business_name"
    assert "'=cmd" in lines[1]
    assert not lines[1].split(",", 1)[1].startswith("=")


def test_render_csv_header_matches_columns() -> None:
    csv_text = render_csv(_sample_data())
    assert csv_text.splitlines()[0] == "id,business_name"


def test_render_xlsx_has_data_and_metadata_sheets() -> None:
    from datetime import UTC, datetime

    content = render_xlsx(
        _sample_data(), generated_at=datetime(2026, 1, 1, tzinfo=UTC), generated_by="test@x.dev"
    )
    workbook = load_workbook(io.BytesIO(content))
    assert set(workbook.sheetnames) == {"Datos", "Metadatos"}

    data_sheet = workbook["Datos"]
    rows = list(data_sheet.iter_rows(values_only=True))
    assert rows[0] == ("id", "business_name")
    assert len(rows) == 3  # header + 2 filas

    metadata_sheet = workbook["Metadatos"]
    metadata_rows = list(metadata_sheet.iter_rows(values_only=True))
    metadata_dict = {row[0]: row[1] for row in metadata_rows}
    assert metadata_dict["Reporte"] == "CLIENTS"
    assert metadata_dict["Filas"] == 2
