import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.obligation import ObligationCatalog
from app.models.tenant import Tenant
from app.models.user import User
from app.seeds.obligation_catalog_data import OBLIGATION_CATALOG_SEED

settings = get_settings()


@pytest.fixture(scope="session")
def test_engine():
    url = settings.test_database_url
    assert url, "TEST_DATABASE_URL debe estar configurado para correr las pruebas"
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        # Requeridas por columnas/constraints del modelo (gen_random_uuid,
        # gen_random_bytes, EXCLUDE USING gist sobre client_id/category_code).
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    connection = test_engine.connect()
    outer_transaction = connection.begin()
    session_factory = sessionmaker(bind=connection, future=True)
    session = session_factory()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def tenant(db_session):
    t = Tenant(name="Tenant de prueba")
    db_session.add(t)
    db_session.flush()
    return t


@pytest.fixture()
def admin_user(db_session, tenant):
    user = User(
        tenant_id=tenant.id,
        email="admin@luisiacontable-tests.dev",
        full_name="Admin de prueba",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def auth_client(client, admin_user):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    # Replica lo que hace el frontend real (src/lib/apiClient.ts): reenvia el
    # token CSRF de la cookie en el header X-CSRF-Token para mutaciones.
    csrf_token = response.cookies.get("csrf_token")
    assert csrf_token, "El login deberia establecer la cookie csrf_token"
    client.headers.update({"X-CSRF-Token": csrf_token})
    return client


@pytest.fixture()
def scratch_database():
    """Base de datos Postgres nueva y desechable, para pruebas que ejercitan
    Alembic directamente (ver tests/integration/test_migrations.py y
    test_migration_pgcrypto_bootstrap.py)."""
    import uuid

    from sqlalchemy import text

    from tests.integration._migration_helpers import admin_engine

    db_name = f"luis_ia_contable_scratch_{uuid.uuid4().hex[:10]}"
    engine = admin_engine()
    with engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()

    yield db_name

    engine = admin_engine()
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture()
def redirected_database_url(scratch_database):
    """Apunta temporalmente settings.database_url (el singleton compartido,
    el mismo que lee migrations/env.py) a la base de datos desechable, y lo
    restaura al finalizar."""
    from tests.integration._migration_helpers import scratch_url as build_scratch_url

    url = build_scratch_url(scratch_database)
    original_url = settings.database_url
    settings.database_url = url
    try:
        yield url
    finally:
        settings.database_url = original_url


@pytest.fixture()
def frozen_clock():
    """Reloj controlable para pruebas (ver ADR-0008). Uso:
    frozen_clock['now'] = frozen_clock['now'] + timedelta(...)"""
    from datetime import UTC, datetime

    from app.core import clock as clock_module

    state = {"now": datetime.now(UTC)}
    clock_module.set_clock(lambda: state["now"])
    yield state
    clock_module.reset_clock()


@pytest.fixture()
def obligation_catalog(db_session):
    items = {}
    for entry in OBLIGATION_CATALOG_SEED:
        item = ObligationCatalog(**entry, is_active=True)
        db_session.add(item)
        db_session.flush()
        items[entry["code"]] = item
    return items
