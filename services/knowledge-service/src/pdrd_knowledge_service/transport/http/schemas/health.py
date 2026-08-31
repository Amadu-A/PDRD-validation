# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/health.py

"""HTTP health schemas Knowledge Service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LiveHealthResponse(BaseModel):
    """Liveness response."""

    model_config = ConfigDict(
        frozen=True,
    )

    status: Literal["ok"] = "ok"

    service: str
    version: str


class ReadyHealthResponse(BaseModel):
    """Readiness response."""

    model_config = ConfigDict(
        frozen=True,
    )

    status: Literal["ready"] = "ready"

    service: str
    version: str

    dependencies: dict[str, bool]
