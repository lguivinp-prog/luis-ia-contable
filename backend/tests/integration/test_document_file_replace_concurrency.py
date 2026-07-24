"""Concurrencia real del reemplazo/eliminacion de archivos documentales
(Bloque 5, Seccion 11 del plan): dos conexiones reales independientes
contra el mismo `test_engine`, sincronizadas con `threading.Event` (nunca
solo `sleep` como sustituto de sincronizacion real) -- mismo patron que
`test_tax_payment_concurrency.py`/`test_users_last_admin_concurrency.py`.
Verifica que el bloqueo del `file_id` EXACTO (no "cualquier ACTIVE de la
cadena") serializa dos peticiones que apuntan a la misma version original:
una gana, la otra ve esa version como SUPERSEDED/DELETED y recibe el error
de dominio que el router traduce a 409 -- nunca queda mas de una version
ACTIVE en la cadena, nunca se pierde un archivo, y no quedan `orphan_blob`
ni `temporary_part` tras el caso normal."""

import io
import threading
import time
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.file_storage import LocalFileStorageBackend
from app.core.security import hash_password
from app.models.declaration_document import ClientDocumentRequirement, DeclarationDocumentItem
from app.models.declaration_document_file import DeclarationDocumentFile
from app.models.obligation import ObligationCatalog
from app.models.tenant import Tenant
from app.models.user import User
from app.seeds.obligation_catalog_data import OBLIGATION_CATALOG_SEED
from app.services import declaration_service, document_file_service
from app.services.document_file_service import DeclarationDocumentFileNotFoundError, FileNotActiveError
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)

_PDF_BYTES = b"%PDF-1.4\n" + b"0" * 100


def _build_scenario(session_factory, storage_root) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia archivos")
        session.add(tenant)
        session.flush()

        admin = User(
            tenant_id=tenant.id,
            email=f"admin-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin concurrencia",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()

        client_row = make_client(session, tenant.id)

        seed = dict(next(e for e in OBLIGATION_CATALOG_SEED if e["code"] == "621"))
        seed["code"] = f"621-{uuid.uuid4().hex[:8]}"  # code es unico global, no por tenant
        obligation = ObligationCatalog(**seed, is_active=True)
        session.add(obligation)
        session.flush()

        co = make_client_obligation(session, tenant.id, client_row.id, obligation.id)
        source = make_source(session, tenant.id, obligation.id)
        version = make_version(session, tenant.id, source.id, fiscal_year=2026)
        make_entry(session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
        period = make_monthly_period(session, tenant.id, fiscal_year=2026, month=1)

        declaration = declaration_service.create_declaration(
            session,
            tenant.id,
            admin.id,
            client_id=client_row.id,
            client_obligation_id=co.id,
            tax_period_id=period.id,
            origin="MANUAL",
            sync_documents=False,
        )

        from app.models.declaration_document import DocumentRequirementCatalog

        catalog_entry = DocumentRequirementCatalog(
            tenant_id=tenant.id, is_system=False, code=f"CAT-{uuid.uuid4().hex[:6]}", name="Catalogo"
        )
        session.add(catalog_entry)
        session.flush()

        requirement = ClientDocumentRequirement(
            tenant_id=tenant.id,
            client_id=client_row.id,
            requirement_catalog_id=catalog_entry.id,
            client_obligation_id=None,
            periodicity="MONTHLY",
            valid_from=period.start_date,
        )
        session.add(requirement)
        session.flush()

        item = DeclarationDocumentItem(
            tenant_id=tenant.id,
            client_id=client_row.id,
            declaration_id=declaration.id,
            client_document_requirement_id=requirement.id,
            status="NOT_REQUESTED",
        )
        session.add(item)
        session.flush()

        storage = LocalFileStorageBackend(storage_root)
        first_file = document_file_service.upload_file(
            session,
            storage,
            tenant.id,
            client_row.id,
            declaration.id,
            item.id,
            stream=io.BytesIO(_PDF_BYTES),
            original_filename="original.pdf",
            description=None,
            max_bytes=50_000_000,
            actor_user_id=admin.id,
        )
        session.commit()

        return {
            "tenant_id": tenant.id,
            "client_id": client_row.id,
            "declaration_id": declaration.id,
            "item_id": item.id,
            "file_id": first_file.id,
            "admin_id": admin.id,
        }
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    session = session_factory()
    try:
        session.execute(
            delete(DeclarationDocumentFile).where(DeclarationDocumentFile.tenant_id == tenant_id)
        )
        session.commit()
    finally:
        session.close()


def test_two_concurrent_replaces_of_same_file_only_one_succeeds(test_engine, tmp_path) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    storage_root = tmp_path / "concurrency_replace"
    scenario = _build_scenario(session_factory, storage_root)
    tenant_id = scenario["tenant_id"]
    file_id = scenario["file_id"]

    a_locked = threading.Event()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def worker(label: str, filename: str, delay: bool) -> None:
        session = session_factory()
        storage = LocalFileStorageBackend(storage_root)
        try:
            if delay:
                a_locked.wait(timeout=5)
            else:
                a_locked.set()
                time.sleep(0.3)  # da tiempo a que el otro hilo quede realmente bloqueado
            document_file_service.replace_file(
                session,
                storage,
                tenant_id,
                scenario["client_id"],
                scenario["declaration_id"],
                scenario["item_id"],
                file_id,
                stream=io.BytesIO(_PDF_BYTES),
                original_filename=filename,
                max_bytes=50_000_000,
                reason=f"reemplazo {label}",
                actor_user_id=scenario["admin_id"],
            )
            session.commit()
            results[label] = "replaced"
        except FileNotActiveError:
            session.rollback()
            results[label] = "blocked"
        except BaseException as exc:  # noqa: BLE001 -- se reporta en el hilo principal
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker, args=("A", "from_a.pdf", False))
    thread_b = threading.Thread(target=worker, args=("B", "from_b.pdf", True))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not errors, f"Errores inesperados en los hilos: {errors}"
    # Ambas peticiones apuntan al MISMO file_id original; una gana (obtiene
    # el bloqueo primero), la otra espera el bloqueo y, al obtenerlo, ve ese
    # file_id ya SUPERSEDED -> bloqueada (409 a nivel de router).
    assert {results.get("A"), results.get("B")} == {"replaced", "blocked"}

    verify_session = session_factory()
    try:
        chain_files = (
            verify_session.query(DeclarationDocumentFile)
            .filter(DeclarationDocumentFile.tenant_id == tenant_id)
            .all()
        )
        active = [f for f in chain_files if f.status == "ACTIVE"]
        superseded = [f for f in chain_files if f.status == "SUPERSEDED"]
        assert len(active) == 1  # nunca mas de una version ACTIVE
        assert len(superseded) == 1
        assert len(chain_files) == 2  # nunca se pierde un archivo (ni se duplica)
        versions = sorted(f.version for f in chain_files)
        assert versions == [1, 2]  # nunca dos filas con el mismo numero de version

        # No quedan orphan_blob ni temporary_part tras el caso normal: cada
        # fila no-DELETED debe tener su blob fisico, y el .part de la
        # perdedora ya se limpio (write_staged/promote solo dejan rastro en
        # storage_key de filas persistidas).
        storage = LocalFileStorageBackend(storage_root)
        for f in chain_files:
            assert storage.exists(f.storage_key)
        all_physical_keys = set(storage.iter_keys(str(tenant_id)))
        persisted_keys = {f.storage_key for f in chain_files}
        assert all_physical_keys == persisted_keys, (
            f"quedaron blobs fisicos sin fila (huerfanos): {all_physical_keys - persisted_keys}"
        )
    finally:
        verify_session.close()

    _cleanup(session_factory, tenant_id)


def test_replace_concurrent_with_delete_only_one_wins(test_engine, tmp_path) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    storage_root = tmp_path / "concurrency_replace_delete"
    scenario = _build_scenario(session_factory, storage_root)
    tenant_id = scenario["tenant_id"]
    file_id = scenario["file_id"]

    a_locked = threading.Event()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def worker_replace() -> None:
        session = session_factory()
        storage = LocalFileStorageBackend(storage_root)
        try:
            a_locked.set()
            time.sleep(0.3)
            document_file_service.replace_file(
                session,
                storage,
                tenant_id,
                scenario["client_id"],
                scenario["declaration_id"],
                scenario["item_id"],
                file_id,
                stream=io.BytesIO(_PDF_BYTES),
                original_filename="replaced.pdf",
                max_bytes=50_000_000,
                reason="reemplazo concurrente",
                actor_user_id=scenario["admin_id"],
            )
            session.commit()
            results["replace"] = "replaced"
        except FileNotActiveError:
            session.rollback()
            results["replace"] = "blocked"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def worker_delete() -> None:
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            document_file_service.delete_file(
                session,
                tenant_id,
                scenario["declaration_id"],
                scenario["item_id"],
                file_id,
                reason="eliminacion concurrente",
                actor_user_id=scenario["admin_id"],
            )
            session.commit()
            results["delete"] = "deleted"
        except FileNotActiveError:
            session.rollback()
            results["delete"] = "blocked"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_replace)
    thread_b = threading.Thread(target=worker_delete)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not errors, f"Errores inesperados en los hilos: {errors}"
    outcomes = [results.get("replace"), results.get("delete")]
    assert outcomes.count("blocked") == 1
    assert sum(1 for o in outcomes if o in ("replaced", "deleted")) == 1

    verify_session = session_factory()
    try:
        chain_files = (
            verify_session.query(DeclarationDocumentFile)
            .filter(DeclarationDocumentFile.tenant_id == tenant_id)
            .all()
        )
        original = next(f for f in chain_files if f.id == file_id)
        active = [f for f in chain_files if f.status == "ACTIVE"]
        deleted = [f for f in chain_files if f.status == "DELETED"]
        superseded = [f for f in chain_files if f.status == "SUPERSEDED"]
        # Nunca queda la cadena en un estado inconsistente (dos ACTIVE, o
        # SUPERSEDED y DELETED simultaneos en la misma fila).
        if results.get("replace") == "replaced":
            assert len(active) == 1
            assert original.status == "SUPERSEDED"
            assert not deleted
        else:
            assert original.status == "DELETED"
            assert not active
            assert not superseded
    finally:
        verify_session.close()

    _cleanup(session_factory, tenant_id)


def test_second_flush_failure_during_replace_reverts_target_to_active_and_compensates_blob(
    test_engine, tmp_path, monkeypatch
) -> None:
    """Fuerza un fallo en el segundo flush (insercion de la nueva version
    ACTIVE) y confirma la secuencia exacta exigida: el rollback exterior
    deja la version anterior de nuevo ACTIVE (sin logica manual), el blob
    fisico nuevo (ya promovido) se compensa, y el error original se
    conserva sin ocultarse."""
    from app.repositories import declaration_document_file_repository as file_repository

    session_factory = sessionmaker(bind=test_engine, future=True)
    storage_root = tmp_path / "concurrency_second_flush_failure"
    scenario = _build_scenario(session_factory, storage_root)
    tenant_id = scenario["tenant_id"]
    file_id = scenario["file_id"]

    session = session_factory()
    storage = LocalFileStorageBackend(storage_root)

    def _boom_create(db, file):
        raise RuntimeError("violacion de constraint simulada en el segundo flush")

    monkeypatch.setattr(file_repository, "create", _boom_create)

    try:
        with pytest.raises(RuntimeError, match="violacion de constraint simulada"):
            document_file_service.replace_file(
                session,
                storage,
                tenant_id,
                scenario["client_id"],
                scenario["declaration_id"],
                scenario["item_id"],
                file_id,
                stream=io.BytesIO(_PDF_BYTES),
                original_filename="fallara.pdf",
                max_bytes=50_000_000,
                reason="fuerza fallo de segundo flush",
                actor_user_id=scenario["admin_id"],
            )
        session.rollback()
    finally:
        session.close()

    monkeypatch.undo()

    verify_session = session_factory()
    try:
        chain_files = (
            verify_session.query(DeclarationDocumentFile)
            .filter(DeclarationDocumentFile.tenant_id == tenant_id)
            .all()
        )
        # Tras el rollback, solo debe existir la fila original -- vuelta a
        # ACTIVE por el propio rollback, sin fila nueva insertada.
        assert len(chain_files) == 1
        assert chain_files[0].id == file_id
        assert chain_files[0].status == "ACTIVE"
        assert chain_files[0].superseded_at is None
    finally:
        verify_session.close()

    _cleanup(session_factory, tenant_id)


def test_delete_unknown_file_id_raises_not_found(test_engine, tmp_path) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    storage_root = tmp_path / "delete_not_found"
    scenario = _build_scenario(session_factory, storage_root)
    tenant_id = scenario["tenant_id"]

    session = session_factory()
    try:
        with pytest.raises(DeclarationDocumentFileNotFoundError):
            document_file_service.delete_file(
                session,
                tenant_id,
                scenario["declaration_id"],
                scenario["item_id"],
                uuid.uuid4(),
                reason="motivo",
                actor_user_id=scenario["admin_id"],
            )
        session.rollback()
    finally:
        session.close()

    _cleanup(session_factory, tenant_id)
