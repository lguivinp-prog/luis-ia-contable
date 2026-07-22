"""Utilidades compartidas para pruebas que ejercitan Alembic directamente
contra una base de datos Postgres nueva y desechable (no la fixture
test_engine/db_session, que usa Base.metadata.create_all en vez de
migraciones reales). Las fixtures que las usan viven en conftest.py."""

from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.core.config import get_settings

settings = get_settings()


def admin_engine():
    return create_engine(settings.database_url, isolation_level="AUTOCOMMIT", future=True)


def scratch_url(db_name: str) -> str:
    # str(url) enmascara la contrasena con "***"; se necesita el valor real
    # para poder conectarse.
    return make_url(settings.database_url).set(database=db_name).render_as_string(
        hide_password=False
    )


def make_alembic_config() -> Config:
    return Config("alembic.ini")
