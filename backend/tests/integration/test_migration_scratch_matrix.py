"""Verificacion integral de migraciones sobre una base de datos Postgres
nueva y desechable (Dominio I, plan de Bloque 3.2): upgrade desde `0007` con
datos preexistentes "estilo 3.1", downgrade `head -> 0007`, re-upgrade a
`head`, y verificacion puntual de CHECK/UNIQUE/EXCLUDE/indice parcial tras
la re-migracion. A diferencia del resto de la suite (que crea el esquema con
`Base.metadata.create_all()`), esta prueba ejercita Alembic real, igual que
`test_migrations.py`/`test_migration_pgcrypto_bootstrap.py`."""

import uuid

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration._migration_helpers import make_alembic_config


def test_upgrade_downgrade_reupgrade_matrix_with_preexisting_3_1_data(
    redirected_database_url,
) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    # 1. Sube hasta 0007 (estado final de 3.1, antes de Bloque 3.2).
    command.upgrade(cfg, "0007")

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))

            tenant_id = conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (gen_random_uuid(), 'T-3.1') RETURNING id")
            ).scalar()
            user_id = conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, tenant_id, email, full_name, password_hash, role, is_active) "
                    "VALUES (gen_random_uuid(), :t, 'legacy31@dominio-ficticio.dev', 'Legacy 3.1', "
                    "'hash', 'admin', true) RETURNING id"
                ),
                {"t": tenant_id},
            ).scalar()
            client_id = conn.execute(
                text(
                    "INSERT INTO clients "
                    "(id, tenant_id, ruc, business_name, person_type, ruc_status, "
                    "fiscal_address_condition, tax_regime, service_status) "
                    "VALUES (gen_random_uuid(), :t, '20600000099', 'Cliente 3.1 preexistente', "
                    "'juridica', 'ACTIVO', 'HABIDO', 'GENERAL', 'activo') RETURNING id"
                ),
                {"t": tenant_id},
            ).scalar()
            obligation_id = conn.execute(
                text(
                    "INSERT INTO obligation_catalog "
                    "(id, code, name, periodicity, source, source_version, is_active) "
                    "VALUES (gen_random_uuid(), 'SCRATCH-621', 'IGV demo', 'mensual', "
                    "'fixture de prueba', 'v1', true) RETURNING id"
                )
            ).scalar()
            client_obligation_id = conn.execute(
                text(
                    "INSERT INTO client_obligations "
                    "(id, tenant_id, client_id, obligation_id, is_active, assigned_at) "
                    "VALUES (gen_random_uuid(), :t, :c, :o, true, now()) RETURNING id"
                ),
                {"t": tenant_id, "c": client_id, "o": obligation_id},
            ).scalar()
            source_id = conn.execute(
                text(
                    "INSERT INTO tax_schedule_sources (id, tenant_id, obligation_id, name, is_active) "
                    "VALUES (gen_random_uuid(), :t, :o, 'Fuente demo', true) RETURNING id"
                ),
                {"t": tenant_id, "o": obligation_id},
            ).scalar()
            version_id = conn.execute(
                text(
                    "INSERT INTO tax_schedule_versions "
                    "(id, tenant_id, source_id, version_label, fiscal_year, schedule_type, status) "
                    "VALUES (gen_random_uuid(), :t, :s, 'v-scratch', 2026, 'GENERAL', 'APPROVED') "
                    "RETURNING id"
                ),
                {"t": tenant_id, "s": source_id},
            ).scalar()
            period_id = conn.execute(
                text(
                    "INSERT INTO tax_periods "
                    "(id, tenant_id, period_type, fiscal_year, month, canonical_code, "
                    "start_date, end_date, is_open) "
                    "VALUES (gen_random_uuid(), :t, 'MONTHLY', 2026, 1, '2026-01', "
                    "'2026-01-01', '2026-01-31', true) RETURNING id"
                ),
                {"t": tenant_id},
            ).scalar()
            entry_id = conn.execute(
                text(
                    "INSERT INTO tax_schedule_entries "
                    "(id, tenant_id, version_id, tax_period, due_date_original, due_date_effective, "
                    "due_date_type) "
                    "VALUES (gen_random_uuid(), :t, :v, '2026-01', '2026-02-12', '2026-02-12', "
                    "'REGULAR') RETURNING id"
                ),
                {"t": tenant_id, "v": version_id},
            ).scalar()
            declaration_id = conn.execute(
                text(
                    "INSERT INTO declarations "
                    "(id, tenant_id, client_id, client_obligation_id, tax_period_id, "
                    "workflow_status, filing_status, tax_payment_status, document_status, "
                    "initial_effective_due_date, current_original_due_date, current_effective_due_date, "
                    "schedule_source_id, schedule_version_id, schedule_rule_type, schedule_entry_id, "
                    "schedule_snapshot, row_version) "
                    "VALUES (gen_random_uuid(), :t, :c, :co, :p, "
                    "'PENDING', 'NOT_FILED', 'NOT_DETERMINED', 'NOT_CONFIGURED', "
                    "'2026-02-12', '2026-02-12', '2026-02-12', "
                    ":src, :ver, 'ENTRY', :entry, '{}'::jsonb, 1) RETURNING id"
                ),
                {
                    "t": tenant_id, "c": client_id, "co": client_obligation_id, "p": period_id,
                    "src": source_id, "ver": version_id, "entry": entry_id,
                },
            ).scalar()
    finally:
        engine.dispose()

    # 2. Sube el resto de la cadena (0008 -> 0010) con la declaracion de
    # "3.1" ya presente -- debe funcionar sin backfill destructivo.
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            declaration_still_there = conn.execute(
                text("SELECT document_status FROM declarations WHERE id = :id"),
                {"id": declaration_id},
            ).scalar_one()
            new_tables = conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN "
                    "('document_requirement_catalog', 'tax_assessments', 'professional_fee_agreements')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert declaration_still_there == "NOT_CONFIGURED"
    assert new_tables == 3

    # 3. Baja de head a 0007 -- debe eliminar limpiamente las 3 migraciones
    # nuevas sin afectar la declaracion preexistente.
    command.downgrade(cfg, "0007")

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            declaration_survived_downgrade = conn.execute(
                text("SELECT id FROM declarations WHERE id = :id"), {"id": declaration_id}
            ).scalar_one()
            gone_tables = conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN "
                    "('document_requirement_catalog', 'tax_assessments', 'professional_fee_agreements')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert declaration_survived_downgrade == declaration_id
    assert gone_tables == 0

    # 4. Re-sube a head -- el ciclo completo debe ser repetible.
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            # --- CHECK: assessment_type/sequence_number incoherentes ---
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO tax_assessments "
                            "(id, tenant_id, declaration_id, assessment_type, sequence_number, "
                            "gross_tax_amount, credits_applied, total_payable, remaining_credit, "
                            "assessed_at, assessed_by, is_current) "
                            "VALUES (gen_random_uuid(), :t, :d, 'ORIGINAL', 2, "
                            "100, 0, 100, 0, now(), :u, true)"
                        ),
                        {"t": tenant_id, "d": declaration_id, "u": user_id},
                    )
                raise AssertionError("Se esperaba IntegrityError por CHECK de tipo/secuencia")
            except IntegrityError:
                pass

            # --- Insercion valida para poder probar UNIQUE despues ---
            assessment_id = conn.execute(
                text(
                    "INSERT INTO tax_assessments "
                    "(id, tenant_id, declaration_id, assessment_type, sequence_number, "
                    "gross_tax_amount, credits_applied, total_payable, remaining_credit, "
                    "assessed_at, assessed_by, is_current) "
                    "VALUES (gen_random_uuid(), :t, :d, 'ORIGINAL', 1, 1000, 0, 1000, 0, "
                    "now(), :u, true) RETURNING id"
                ),
                {"t": tenant_id, "d": declaration_id, "u": user_id},
            ).scalar()

            # --- UNIQUE(tenant_id, declaration_id, sequence_number) ---
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO tax_assessments "
                            "(id, tenant_id, declaration_id, assessment_type, sequence_number, "
                            "gross_tax_amount, credits_applied, total_payable, remaining_credit, "
                            "assessed_at, assessed_by, is_current) "
                            "VALUES (gen_random_uuid(), :t, :d, 'ORIGINAL', 1, 500, 0, 500, 0, "
                            "now(), :u, false)"
                        ),
                        {"t": tenant_id, "d": declaration_id, "u": user_id},
                    )
                raise AssertionError("Se esperaba IntegrityError por UNIQUE(sequence_number)")
            except IntegrityError:
                pass

            # --- idempotency_key UNIQUE en tax_payments ---
            shared_key = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO tax_payments "
                    "(id, tenant_id, declaration_id, assessment_id, paid_at, amount, "
                    "payment_method, created_by, idempotency_key) "
                    "VALUES (gen_random_uuid(), :t, :d, :a, '2026-02-01', 100, 'TRANSFER', :u, :k)"
                ),
                {"t": tenant_id, "d": declaration_id, "a": assessment_id, "u": user_id, "k": shared_key},
            )
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO tax_payments "
                            "(id, tenant_id, declaration_id, assessment_id, paid_at, amount, "
                            "payment_method, created_by, idempotency_key) "
                            "VALUES (gen_random_uuid(), :t, :d, :a, '2026-02-02', 200, 'TRANSFER', :u, :k)"
                        ),
                        {
                            "t": tenant_id, "d": declaration_id, "a": assessment_id,
                            "u": user_id, "k": shared_key,
                        },
                    )
                raise AssertionError("Se esperaba IntegrityError por UNIQUE(idempotency_key)")
            except IntegrityError:
                pass

            # --- EXCLUDE USING gist en professional_fee_agreements ---
            conn.execute(
                text(
                    "INSERT INTO professional_fee_agreements "
                    "(id, tenant_id, client_id, service_type, amount, status, valid_from) "
                    "VALUES (gen_random_uuid(), :t, :c, 'CONTABILIDAD_SCRATCH', 100, 'ACTIVE', "
                    "'2026-01-01')"
                ),
                {"t": tenant_id, "c": client_id},
            )
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO professional_fee_agreements "
                            "(id, tenant_id, client_id, service_type, amount, status, valid_from) "
                            "VALUES (gen_random_uuid(), :t, :c, 'CONTABILIDAD_SCRATCH', 200, 'ACTIVE', "
                            "'2026-03-01')"
                        ),
                        {"t": tenant_id, "c": client_id},
                    )
                raise AssertionError("Se esperaba IntegrityError por EXCLUDE de vigencia solapada")
            except IntegrityError:
                pass

            # --- indice unico parcial: una sola fila is_current=true por declaracion ---
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO tax_assessments "
                            "(id, tenant_id, declaration_id, assessment_type, sequence_number, "
                            "gross_tax_amount, credits_applied, total_payable, remaining_credit, "
                            "assessed_at, assessed_by, is_current) "
                            "VALUES (gen_random_uuid(), :t, :d, 'CORRECTION', 2, 900, 0, 900, 0, "
                            "now(), :u, true)"
                        ),
                        {"t": tenant_id, "d": declaration_id, "u": user_id},
                    )
                raise AssertionError("Se esperaba IntegrityError por indice unico parcial is_current")
            except IntegrityError:
                pass
    finally:
        engine.dispose()
