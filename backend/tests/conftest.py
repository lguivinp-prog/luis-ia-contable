import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
    return client


@pytest.fixture()
def obligation_catalog(db_session):
    items = {}
    for entry in OBLIGATION_CATALOG_SEED:
        item = ObligationCatalog(**entry, is_active=True)
        db_session.add(item)
        db_session.flush()
        items[entry["code"]] = item
    return items
