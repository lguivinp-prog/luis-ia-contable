"""Carga datos ficticios de desarrollo. Idempotente: puede ejecutarse varias veces
sin duplicar registros. NUNCA usar en un entorno con datos reales.

Ejecucion: python -m app.seeds.seed_dev_data
"""

import os
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.client import Client
from app.models.obligation import ClientObligation, ObligationCatalog
from app.models.tenant import Tenant
from app.models.user import User
from app.seeds.obligation_catalog_data import OBLIGATION_CATALOG_SEED
from app.services.ruc_validator import compute_check_digit

DEFAULT_TENANT_NAME = "Estudio Contable Demo"

FICTITIOUS_CLIENTS = [
    {
        "ruc_prefix": "20",
        "ruc_base": "60000001",
        "business_name": "CONSTRUCTORA FICTICIA DEL PACIFICO S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
        "service_status": "activo",
        "monthly_fee": "450.00",
        "payment_day": 5,
        "obligation_codes": ["621", "PLAME", "SIRE-RVIE", "SIRE-RCE", "710"],
    },
    {
        "ruc_prefix": "20",
        "ruc_base": "60000002",
        "business_name": "COMERCIALIZADORA DEMO ANDINA E.I.R.L.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "RER",
        "service_status": "activo",
        "monthly_fee": "250.00",
        "payment_day": 10,
        "obligation_codes": ["621", "SIRE-RVIE", "SIRE-RCE"],
    },
    {
        "ruc_prefix": "10",
        "ruc_base": "60000003",
        "business_name": "JUAN PEREZ EJEMPLO",
        "person_type": "natural",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "NRUS",
        "service_status": "prospecto",
        "monthly_fee": None,
        "payment_day": None,
        "obligation_codes": [],
    },
]


def _build_fake_ruc(prefix: str, base8: str) -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def seed_tenant(db) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.name == DEFAULT_TENANT_NAME)).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=DEFAULT_TENANT_NAME)
        db.add(tenant)
        db.flush()
        print(f"[seed] Tenant creado: {tenant.name}")
    else:
        print(f"[seed] Tenant ya existia: {tenant.name}")
    return tenant


def seed_admin_user(db, tenant: Tenant) -> User:
    email = os.environ.get("DEV_SEED_ADMIN_EMAIL", "admin@luisiacontable.dev")
    password = os.environ.get("DEV_SEED_ADMIN_PASSWORD")
    if not password:
        print("[seed] ERROR: DEV_SEED_ADMIN_PASSWORD no esta definido en el entorno.", file=sys.stderr)
        sys.exit(1)

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name="Administrador (desarrollo)",
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.flush()
        print(f"[seed] Usuario administrador creado: {email}")
    else:
        print(f"[seed] Usuario administrador ya existia: {email}")
    return user


def seed_obligation_catalog(db) -> dict[str, ObligationCatalog]:
    catalog_by_code: dict[str, ObligationCatalog] = {}
    for entry in OBLIGATION_CATALOG_SEED:
        item = db.execute(
            select(ObligationCatalog).where(ObligationCatalog.code == entry["code"])
        ).scalar_one_or_none()
        if item is None:
            item = ObligationCatalog(**entry, is_active=True)
            db.add(item)
            db.flush()
            print(f"[seed] Obligacion agregada al catalogo: {entry['code']}")
        catalog_by_code[entry["code"]] = item
    return catalog_by_code


def seed_clients(db, tenant: Tenant, user: User, catalog_by_code: dict[str, ObligationCatalog]) -> None:
    for spec in FICTITIOUS_CLIENTS:
        ruc = _build_fake_ruc(spec["ruc_prefix"], spec["ruc_base"])
        client = db.execute(
            select(Client).where(Client.tenant_id == tenant.id, Client.ruc == ruc)
        ).scalar_one_or_none()
        if client is None:
            client = Client(
                tenant_id=tenant.id,
                ruc=ruc,
                business_name=spec["business_name"],
                person_type=spec["person_type"],
                ruc_status=spec["ruc_status"],
                fiscal_address_condition=spec["fiscal_address_condition"],
                tax_regime=spec["tax_regime"],
                service_status=spec["service_status"],
                monthly_fee=spec["monthly_fee"],
                payment_day=spec["payment_day"],
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(client)
            db.flush()
            print(f"[seed] Contribuyente ficticio creado: {spec['business_name']} ({ruc})")
        else:
            print(f"[seed] Contribuyente ficticio ya existia: {spec['business_name']} ({ruc})")

        for code in spec["obligation_codes"]:
            obligation = catalog_by_code[code]
            existing = db.execute(
                select(ClientObligation).where(
                    ClientObligation.client_id == client.id,
                    ClientObligation.obligation_id == obligation.id,
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    ClientObligation(
                        tenant_id=tenant.id,
                        client_id=client.id,
                        obligation_id=obligation.id,
                        assigned_at=datetime.now(UTC),
                    )
                )
                print(f"[seed]   Obligacion {code} asignada a {spec['business_name']}")


def main() -> None:
    db = SessionLocal()
    try:
        tenant = seed_tenant(db)
        user = seed_admin_user(db, tenant)
        catalog_by_code = seed_obligation_catalog(db)
        seed_clients(db, tenant, user, catalog_by_code)
        db.commit()
        print("[seed] Completado correctamente.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
