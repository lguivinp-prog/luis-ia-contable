import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: str
    format: Literal["csv", "xlsx"]
    tax_period_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
