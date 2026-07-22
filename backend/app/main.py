from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import get_dummy_password_hash
from app.routers import auth, clients, health, obligations, tax_categories, tax_schedules

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
