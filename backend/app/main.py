from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import get_dummy_password_hash
from app.routers import (
    auth,
    clients,
    dashboard,
    declaration_documents,
    declaration_generation,
    declarations,
    document_integrity,
    google_integrations,
    health,
    obligations,
    operational_tasks,
    professional_fees,
    reports,
    tax_assessments,
    tax_categories,
    tax_payments,
    tax_periods,
    tax_schedules,
    users,
)

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="Luis IA Contable API",
    version="0.1.0",
    description="API de control contable, tributario y documental para un contador independiente peruano.",
)


@app.on_event("startup")
def _warm_up_caches() -> None:
    # Precalienta el hash ficticio usado para mitigar enumeracion por tiempo
    # en el login (ver ADR-0009); evita calcularlo en la primera solicitud.
    get_dummy_password_hash()


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(obligations.router)
app.include_router(tax_categories.router)
app.include_router(tax_schedules.router)
app.include_router(tax_periods.router)
app.include_router(declarations.router)
app.include_router(declaration_generation.router)
app.include_router(declaration_documents.router)
app.include_router(tax_assessments.router)
app.include_router(tax_payments.router)
app.include_router(professional_fees.router)
app.include_router(reports.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(document_integrity.router)
app.include_router(operational_tasks.router)
app.include_router(google_integrations.router)
