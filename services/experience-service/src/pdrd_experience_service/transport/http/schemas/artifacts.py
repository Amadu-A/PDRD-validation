# services/experience-service/src/pdrd_experience_service/transport/http/schemas/artifacts.py

"""Пользователь не назначает status, collection, manifest или оценку качества."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pdrd_experience_service.transport.http.schemas.catalog import ExampleReference


class CreateVersion(BaseModel):
    """Ручной запуск ограниченного набора выбранных редакций."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["vector", "fine_tune"]
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    items: list[ExampleReference] = Field(min_length=1, max_length=1000)


class ChangeVersion(BaseModel):
    """Отдельная CAS-ревизия версии."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    name: str = Field(default="", max_length=200)


class WorkerClaim(BaseModel):
    """Модель идентифицирует совместимый worker, а не браузер."""

    model_config = ConfigDict(extra="forbid")
    worker: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    model: str = Field(min_length=1, max_length=200)
    identity: str = Field(pattern=r"^[a-f0-9]{16}$")
    dimension: int = Field(ge=1, le=65536, strict=True)


class WorkerResult(BaseModel):
    """Worker сообщает готовность, но не разрешает использование в рабочем анализе."""

    model_config = ConfigDict(extra="forbid")
    worker: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    status: Literal["heartbeat", "ready", "failed"]
    error: str = Field(default="", max_length=500)
