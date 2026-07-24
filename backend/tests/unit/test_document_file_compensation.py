"""Compensacion de almacenamiento fisico ante fallo de `flush()` (Bloque 5,
Seccion 5/10 del plan): si la insercion de la fila falla despues de que el
blob ya fue promovido a su clave definitiva, el archivo fisico se limpia y
el error original de la base de datos se sigue propagando sin ocultarse."""

import io

import pytest

from app.core.file_storage import LocalFileStorageBackend, compensate_physical_write


class _BoomStorage:
    """Backend de almacenamiento cuyo `delete()` siempre falla -- simula el
    caso "la propia compensacion tambien falla"."""

    def delete(self, key: str) -> None:
        raise OSError("disco no disponible")


def test_compensate_physical_write_deletes_the_key(tmp_path) -> None:
    storage = LocalFileStorageBackend(tmp_path / "storage")
    staged = storage.write_staged("a/v1.part", io.BytesIO(b"contenido"), max_bytes=1000)
    storage.promote(staged.temporary_key, "a/v1-final.pdf")
    assert storage.exists("a/v1-final.pdf")

    compensate_physical_write(storage, key="a/v1-final.pdf")
    assert not storage.exists("a/v1-final.pdf")


def test_compensate_physical_write_never_raises_when_delete_fails() -> None:
    # Ni siquiera si storage.delete() lanza, compensate_physical_write debe
    # tragarse el error (best-effort) -- el reporte de integridad es la
    # segunda barrera, nunca este helper.
    compensate_physical_write(_BoomStorage(), key="cualquier/clave")


def test_upload_flush_failure_compensates_physical_blob_and_reraises(tmp_path, monkeypatch) -> None:
    import uuid

    from app.repositories import declaration_document_file_repository as file_repository
    from app.services import document_file_service

    storage = LocalFileStorageBackend(tmp_path / "storage")

    class _FakeSession:
        def add(self, obj):
            pass

        def flush(self):
            raise RuntimeError("violacion de constraint simulada")

    def _boom_create(db, file):
        raise RuntimeError("violacion de constraint simulada")

    monkeypatch.setattr(file_repository, "create", _boom_create)

    with pytest.raises(RuntimeError, match="violacion de constraint simulada"):
        document_file_service.upload_file(
            _FakeSession(),
            storage,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            stream=io.BytesIO(b"%PDF-1.4\n" + b"0" * 50),
            original_filename="a.pdf",
            description=None,
            max_bytes=1_000_000,
            actor_user_id=uuid.uuid4(),
        )

    # El blob definitivo (ya promovido antes del fallo) debe haberse limpiado.
    remaining = list(storage.iter_keys(""))
    assert remaining == []
