import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.declaration_document_file import DeclarationDocumentFile


def create(db: Session, file: DeclarationDocumentFile) -> DeclarationDocumentFile:
    db.add(file)
    db.flush()
    return file


def get(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID, file_id: uuid.UUID
) -> DeclarationDocumentFile | None:
    return db.execute(
        select(DeclarationDocumentFile).where(
            DeclarationDocumentFile.tenant_id == tenant_id,
            DeclarationDocumentFile.declaration_id == declaration_id,
            DeclarationDocumentFile.declaration_document_item_id == item_id,
            DeclarationDocumentFile.id == file_id,
        )
    ).scalar_one_or_none()


def list_for_item(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID
) -> list[DeclarationDocumentFile]:
    return list(
        db.execute(
            select(DeclarationDocumentFile)
            .where(
                DeclarationDocumentFile.tenant_id == tenant_id,
                DeclarationDocumentFile.declaration_id == declaration_id,
                DeclarationDocumentFile.declaration_document_item_id == item_id,
            )
            .order_by(DeclarationDocumentFile.logical_file_id, DeclarationDocumentFile.version)
        )
        .scalars()
        .all()
    )


def lock_exact_active_file(
    db: Session,
    tenant_id: uuid.UUID,
    declaration_id: uuid.UUID,
    item_id: uuid.UUID,
    file_id: uuid.UUID,
) -> DeclarationDocumentFile | None:
    """Bloquea el `file_id` EXACTO solicitado (no "cualquier ACTIVE de la
    cadena") -- garantiza que dos peticiones que apuntan a la misma version
    original no puedan encadenarse silenciosamente una detras de otra (ver
    Seccion 2/11 del plan de Bloque 5). Devuelve `None` si no existe o no
    pertenece al tenant/declaracion/item indicados; el llamador decide si
    eso es 404. No filtra por `status` aqui -- el llamador confirma
    `status == ACTIVE` bajo el bloqueo ya tomado y decide el 409.

    `populate_existing()` es obligatorio: si el llamador ya tenia este
    `file_id` en el identity map de la sesion (p. ej. por el probe sin
    bloqueo de `replace_file`, que lee el `logical_file_id`/`version` ANTES
    de bloquear), SQLAlchemy devolveria por defecto el objeto Python en
    cache SIN refrescar sus columnas -- aunque el `SELECT ... FOR UPDATE`
    bloqueo correctamente la fila a nivel SQL y espero a que la otra
    transaccion (que si modifico la fila) hiciera commit, el atributo
    `.status` en memoria seguiria reflejando el valor previo al bloqueo.
    Eso permitiria que el llamador crea erroneamente que la version sigue
    ACTIVE cuando en realidad ya fue reemplazada/eliminada por la otra
    transaccion -- exactamente la corrida de datos que este bloqueo exacto
    existe para impedir."""
    return db.execute(
        select(DeclarationDocumentFile)
        .where(
            DeclarationDocumentFile.tenant_id == tenant_id,
            DeclarationDocumentFile.declaration_id == declaration_id,
            DeclarationDocumentFile.declaration_document_item_id == item_id,
            DeclarationDocumentFile.id == file_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def iter_all_non_deleted_for_tenant(db: Session, tenant_id: uuid.UUID):
    """Usado por el reporte de integridad (Dominio F): recorre todas las
    filas ACTIVE/SUPERSEDED del tenant (nunca DELETED, que se trata aparte
    como `retained_deleted_blob`)."""
    return (
        db.execute(
            select(DeclarationDocumentFile).where(
                DeclarationDocumentFile.tenant_id == tenant_id,
                DeclarationDocumentFile.status.in_(["ACTIVE", "SUPERSEDED"]),
            )
        )
        .scalars()
        .all()
    )


def iter_deleted_for_tenant(db: Session, tenant_id: uuid.UUID):
    return (
        db.execute(
            select(DeclarationDocumentFile).where(
                DeclarationDocumentFile.tenant_id == tenant_id,
                DeclarationDocumentFile.status == "DELETED",
            )
        )
        .scalars()
        .all()
    )
