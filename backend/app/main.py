from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.routers import auth, clients, health, obligations

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="Luis IA Contable API",
    version="0.1.0",
    description="API de control contable, tributario y documental para un contador independiente peruano.",
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(obligations.router)
