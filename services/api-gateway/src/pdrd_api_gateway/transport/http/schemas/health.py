# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/health.py

"""HTTP response-схемы health endpoints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LiveHealthResponse(BaseModel):
    """Ответ liveness probe API Gateway."""

    model_config = ConfigDict(
        frozen=True,
    )

    status: Literal["ok"] = "ok"
    service: str
    version: str


class DependenciesHealthResponse(BaseModel):
    """Состояние обязательных infrastructure dependencies."""

    model_config = ConfigDict(
        frozen=True,
    )

    database: Literal["ok"] = "ok"
    broker: Literal["ok"] = "ok"


class ReadyHealthResponse(BaseModel):
    """Ответ readiness probe API Gateway."""

    model_config = ConfigDict(
        frozen=True,
    )

    status: Literal["ready"] = "ready"
    service: str
    version: str
    environment: str

    dependencies: DependenciesHealthResponse
