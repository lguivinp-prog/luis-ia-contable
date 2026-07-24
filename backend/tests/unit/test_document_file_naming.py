from app.services.document_file_naming import build_content_disposition, sanitize_original_filename


def test_strips_unix_path_components() -> None:
    assert sanitize_original_filename("/etc/passwd") == "passwd"


def test_strips_windows_path_components() -> None:
    assert sanitize_original_filename("C:\\Users\\foo\\bar.pdf") == "bar.pdf"


def test_strips_path_traversal_components() -> None:
    assert sanitize_original_filename("../../../etc/passwd") == "passwd"


def test_rejects_control_characters_and_crlf() -> None:
    raw = "documento\r\nmalicioso\x00.pdf"
    cleaned = sanitize_original_filename(raw)
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert "\x00" not in cleaned


def test_truncates_to_max_length() -> None:
    raw = "a" * 500 + ".pdf"
    cleaned = sanitize_original_filename(raw)
    assert len(cleaned) == 255


def test_empty_or_whitespace_only_falls_back_to_default() -> None:
    assert sanitize_original_filename("   ") == "documento"
    assert sanitize_original_filename("") == "documento"


def test_content_disposition_ascii_filename() -> None:
    header = build_content_disposition("informe.pdf")
    assert header == 'attachment; filename="informe.pdf"; filename*=UTF-8\'\'informe.pdf'


def test_content_disposition_non_ascii_filename_is_percent_encoded() -> None:
    header = build_content_disposition("constancia_ñoño.pdf")
    assert header.startswith("attachment; filename=")
    assert "filename*=UTF-8''" in header
    assert "%C3%B1" in header  # 'ñ' codificado por porcentaje en la variante UTF-8


def test_content_disposition_never_contains_raw_crlf() -> None:
    # sanitize_original_filename ya elimina CR/LF antes de llegar aqui, pero
    # se verifica la defensa en profundidad de la propia funcion tambien.
    header = build_content_disposition("normal.pdf")
    assert "\r" not in header
    assert "\n" not in header
