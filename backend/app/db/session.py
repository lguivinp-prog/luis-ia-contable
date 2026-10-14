from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# `hide_parameters`: una excepcion de base de datos jamas vuelca los valores enlazados (cifrado, nombres de perfil, motivos) a los registros.
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, hide_parameters=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
