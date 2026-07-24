"""Validacion de contenido real de archivos documentales (Bloque 5,
ADR-0056): firmas PDF/JPEG/PNG, verificacion estructural real de OOXML
(no solo "es un ZIP"), rechazo de macros, ambiguedad word/xl, y limites
anti zip-bomb."""

import io
import zipfile

import pytest

from app.services.document_file_content_validation import (
    UnsupportedFileContentError,
    classify_file_content,
)


def _stream(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


def test_pdf_signature_detected() -> None:
    result = classify_file_content(_stream(b"%PDF-1.4\n" + b"0" * 100))
    assert result.mime_type == "application/pdf"
    assert result.extension == ".pdf"


def test_jpeg_signature_detected() -> None:
    result = classify_file_content(_stream(b"\xff\xd8\xff\xe0" + b"0" * 100))
    assert result.mime_type == "image/jpeg"
    assert result.extension == ".jpg"


def test_png_signature_detected() -> None:
    result = classify_file_content(_stream(b"\x89PNG\r\n\x1a\n" + b"0" * 100))
    assert result.mime_type == "image/png"
    assert result.extension == ".png"


def test_unknown_content_rejected() -> None:
    with pytest.raises(UnsupportedFileContentError):
        classify_file_content(_stream(b"cualquier texto plano sin firma reconocida"))


def _build_zip(entries: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_valid_docx_structure_accepted() -> None:
    data = _build_zip(
        {"[Content_Types].xml": "<Types xmlns='x'></Types>", "word/document.xml": "<document/>"}
    )
    result = classify_file_content(_stream(data))
    assert result.mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert result.extension == ".docx"


def test_valid_xlsx_structure_accepted() -> None:
    data = _build_zip(
        {"[Content_Types].xml": "<Types xmlns='x'></Types>", "xl/workbook.xml": "<workbook/>"}
    )
    result = classify_file_content(_stream(data))
    assert result.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert result.extension == ".xlsx"


def test_generic_zip_without_ooxml_structure_rejected() -> None:
    data = _build_zip({"hello.txt": "no soy un docx/xlsx real"})
    with pytest.raises(UnsupportedFileContentError, match="Content_Types"):
        classify_file_content(_stream(data))


def test_ambiguous_word_and_excel_prefixes_rejected() -> None:
    data = _build_zip(
        {
            "[Content_Types].xml": "<Types xmlns='x'></Types>",
            "word/document.xml": "<document/>",
            "xl/workbook.xml": "<workbook/>",
        }
    )
    with pytest.raises(UnsupportedFileContentError, match="ambigua"):
        classify_file_content(_stream(data))


def test_macro_enabled_via_vba_project_rejected() -> None:
    data = _build_zip(
        {
            "[Content_Types].xml": "<Types xmlns='x'></Types>",
            "word/document.xml": "<document/>",
            "word/vbaProject.bin": b"macro-bytes",
        }
    )
    with pytest.raises(UnsupportedFileContentError, match="macros"):
        classify_file_content(_stream(data))


def test_macro_enabled_via_content_types_declaration_rejected() -> None:
    data = _build_zip(
        {
            "[Content_Types].xml": (
                "<Types xmlns='x'>"
                "<Override PartName='/word/document.xml' "
                "ContentType='application/vnd.ms-word.document.macroEnabled.main+xml'/>"
                "</Types>"
            ),
            "word/document.xml": "<document/>",
        }
    )
    with pytest.raises(UnsupportedFileContentError, match="macros"):
        classify_file_content(_stream(data))


def test_bad_zip_file_rejected() -> None:
    # Encabezado ZIP valido pero contenido truncado/corrupto.
    with pytest.raises(UnsupportedFileContentError):
        classify_file_content(_stream(b"PK\x03\x04" + b"\x00" * 20))


def test_too_many_zip_entries_rejected() -> None:
    entries = {"[Content_Types].xml": "<Types xmlns='x'></Types>", "word/document.xml": "<document/>"}
    for i in range(2100):
        entries[f"word/media/img{i}.bin"] = "x"
    data = _build_zip(entries)
    with pytest.raises(UnsupportedFileContentError, match="demasiadas entradas"):
        classify_file_content(_stream(data))


def test_zip_entry_name_traversal_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types xmlns='x'></Types>")
        zf.writestr("word/document.xml", "<document/>")
        # zipfile normaliza algunos '..' al escribir; forzamos un nombre
        # crudo escribiendo directamente el ZipInfo.
        info = zipfile.ZipInfo("word/../../../etc/passwd")
        zf.writestr(info, "x")
    with pytest.raises(UnsupportedFileContentError, match="traversal"):
        classify_file_content(_stream(buf.getvalue()))


def test_suspicious_compression_ratio_rejected() -> None:
    # 10 MB de un solo byte repetido comprime extremadamente bien -> ratio
    # sospechoso (simula el patron de un zip-bomb), muy por encima del
    # limite de 100:1 verificado antes de descomprimir nada.
    entries_data = b"0" * (10 * 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types xmlns='x'></Types>")
        zf.writestr("word/document.xml", "<document/>")
        zf.writestr("word/media/big.bin", entries_data)
    with pytest.raises(UnsupportedFileContentError, match="compresion sospechosa"):
        classify_file_content(_stream(buf.getvalue()))
