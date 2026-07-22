from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant


def get_default(db: Session) -> Tenant | None:
    """En esta fase solo existe un tenant; se usa como referencia para auditar
    intentos de login fallidos cuyo correo no corresponde a ningun usuario."""
    return db.execute(select(Tenant).order_by(Tenant.created_at).limit(1)).scalar_one_or_none()
