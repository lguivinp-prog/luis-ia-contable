
from pydantic import BaseModel

from app.schemas.declaration import DeclarationOut


class DashboardRow(DeclarationOut):
    pass


class DashboardOut(BaseModel):
    items: list[DashboardRow]
    total: int
    limit: int
    offset: int
