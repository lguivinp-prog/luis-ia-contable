"""CLI de archivado/restauracion del volumen documental (Bloque 5, Dominio F,
Seccion 14 del plan). El flujo de backup/restauracion completo (BD+volumen,
backend detenido, manifiesto) se verifica manualmente segun el plan -- estas
pruebas cubren solo la logica pura de `archive`/`extract` sobre el sistema de
archivos, sin tocar la base de datos."""

import json
import os
import shutil

from app.cli import document_storage_archive as cli
from app.core.config import get_settings


def test_archive_then_extract_round_trip(tmp_path, monkeypatch, capsys) -> None:
    storage_root = tmp_path / "documents"
    nested = storage_root / "tenant" / "item" / "chain"
    nested.mkdir(parents=True)
    blob = nested / "v1-abc.pdf"
    blob.write_bytes(b"%PDF-1.4\ncontenido de prueba")

    monkeypatch.setattr(get_settings(), "document_storage_root", str(storage_root))

    archive_output = tmp_path / "backup" / "volume.tar.gz"
    rc = cli.main(["archive", "--output", str(archive_output)])
    assert rc == 0
    archive_result = json.loads(capsys.readouterr().out)
    assert archive_result["blob_count"] == 1
    assert archive_output.exists()
    assert len(archive_result["volume_sha256"]) == 64

    # Simula perdida del volumen original y restaura desde el archivo.
    shutil.rmtree(storage_root)

    rc = cli.main(["extract", "--input", str(archive_output), "--confirm"])
    assert rc == 0
    extract_result = json.loads(capsys.readouterr().out)
    assert extract_result["blob_count"] == 1

    restored_blob = storage_root / "tenant" / "item" / "chain" / "v1-abc.pdf"
    assert restored_blob.read_bytes() == b"%PDF-1.4\ncontenido de prueba"


def test_extract_requires_confirm_flag(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(get_settings(), "document_storage_root", str(tmp_path / "documents"))
    rc = cli.main(["extract", "--input", str(tmp_path / "volume.tar.gz")])
    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert "error" in result


def test_extract_rejects_missing_input_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(get_settings(), "document_storage_root", str(tmp_path / "documents"))
    rc = cli.main(["extract", "--input", str(tmp_path / "no-existe.tar.gz"), "--confirm"])
    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert "error" in result


def test_archive_of_missing_root_produces_zero_blobs(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(get_settings(), "document_storage_root", str(tmp_path / "no_creada_aun"))
    rc = cli.main(["archive", "--output", str(tmp_path / "backup" / "volume.tar.gz")])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["blob_count"] == 0


def test_extract_preserves_root_directory_identity_and_replaces_contents(
    tmp_path, monkeypatch, capsys
) -> None:
    """Regresion: `extract` nunca debe intentar eliminar la raiz de
    almacenamiento en si -- solo su contenido. Cuando esa raiz es el punto de
    montaje de un volumen Docker nombrado (el caso real de produccion), el SO
    rechaza `rmdir`/`rmtree` sobre el punto de montaje aunque ya este vacio
    (visto en verificacion manual: "OSError: Device or resource busy"). Se
    verifica aqui comparando el inodo de la raiz antes/despues de `extract`:
    debe ser el mismo directorio fisico, nunca uno recreado."""
    storage_root = tmp_path / "documents"
    storage_root.mkdir()
    stale = storage_root / "viejo.txt"
    stale.write_bytes(b"contenido obsoleto que debe desaparecer")
    root_inode_before = os.stat(storage_root).st_ino

    monkeypatch.setattr(get_settings(), "document_storage_root", str(storage_root))

    fresh_root = tmp_path / "fresh_source"
    (fresh_root / "tenant").mkdir(parents=True)
    (fresh_root / "tenant" / "nuevo.pdf").write_bytes(b"%PDF-1.4\ncontenido nuevo")
    monkeypatch.setattr(get_settings(), "document_storage_root", str(fresh_root))
    archive_output = tmp_path / "backup" / "volume.tar.gz"
    rc = cli.main(["archive", "--output", str(archive_output)])
    assert rc == 0
    capsys.readouterr()

    monkeypatch.setattr(get_settings(), "document_storage_root", str(storage_root))
    rc = cli.main(["extract", "--input", str(archive_output), "--confirm"])
    assert rc == 0
    capsys.readouterr()

    assert os.stat(storage_root).st_ino == root_inode_before
    assert not stale.exists()
    assert (storage_root / "tenant" / "nuevo.pdf").read_bytes() == b"%PDF-1.4\ncontenido nuevo"
