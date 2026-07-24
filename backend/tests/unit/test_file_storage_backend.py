"""Backend de almacenamiento local (Bloque 5, ADR-0053): escritura en
streaming con limite aplicado durante la lectura, promocion atomica,
compensacion, enumeracion por prefijo y metadatos fisicos minimos."""

import io

import pytest

from app.core.file_storage import EmptyBlobError, LocalFileStorageBackend, StorageLimitExceededError


@pytest.fixture()
def backend(tmp_path):
    return LocalFileStorageBackend(tmp_path / "storage")


def test_write_staged_computes_hash_and_size(backend) -> None:
    data = b"contenido de prueba" * 10
    staged = backend.write_staged("a/b/v1.part", io.BytesIO(data), max_bytes=10_000)
    assert staged.size_bytes == len(data)
    assert len(staged.sha256_hash) == 64
    assert backend.exists("a/b/v1.part")


def test_promote_moves_temporary_to_final_key(backend) -> None:
    staged = backend.write_staged("a/v1.part", io.BytesIO(b"hola"), max_bytes=1000)
    backend.promote(staged.temporary_key, "a/v1-final.pdf")
    assert backend.exists("a/v1-final.pdf")
    assert not backend.exists(staged.temporary_key)


def test_write_staged_enforces_limit_during_read_not_just_content_length(backend) -> None:
    big_data = b"x" * 2000
    with pytest.raises(StorageLimitExceededError):
        backend.write_staged("a/oversized.part", io.BytesIO(big_data), max_bytes=1000)
    # El .part se limpia -- no queda huerfano tras el rechazo.
    assert not backend.exists("a/oversized.part")


def test_write_staged_rejects_empty_file(backend) -> None:
    with pytest.raises(EmptyBlobError):
        backend.write_staged("a/empty.part", io.BytesIO(b""), max_bytes=1000)
    assert not backend.exists("a/empty.part")


def test_delete_is_idempotent_on_missing_key(backend) -> None:
    backend.delete("no/existe.bin")  # no debe lanzar


def test_open_reads_back_exact_bytes(backend) -> None:
    data = b"contenido binario \x00\x01\x02"
    staged = backend.write_staged("a/v1.part", io.BytesIO(data), max_bytes=1000)
    with backend.open(staged.temporary_key) as fh:
        assert fh.read() == data


def test_iter_keys_lists_only_files_under_prefix(backend) -> None:
    backend.write_staged("tenant1/item1/v1.part", io.BytesIO(b"x"), max_bytes=1000)
    backend.write_staged("tenant1/item2/v1.part", io.BytesIO(b"y"), max_bytes=1000)
    backend.write_staged("tenant2/item1/v1.part", io.BytesIO(b"z"), max_bytes=1000)

    keys_tenant1 = set(backend.iter_keys("tenant1"))
    assert keys_tenant1 == {"tenant1/item1/v1.part", "tenant1/item2/v1.part"}


def test_iter_keys_empty_prefix_returns_nothing(backend) -> None:
    assert list(backend.iter_keys("no/existe")) == []


def test_stat_returns_size_and_mtime(backend) -> None:
    staged = backend.write_staged("a/v1.part", io.BytesIO(b"12345"), max_bytes=1000)
    blob_stat = backend.stat(staged.temporary_key)
    assert blob_stat.size_bytes == 5
    assert blob_stat.modified_at > 0


def test_resolve_rejects_keys_escaping_storage_root(backend) -> None:
    with pytest.raises(ValueError, match="fuera de la raiz"):
        backend.exists("../../etc/passwd")
