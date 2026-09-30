# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/experience_versions.py

"""Команды реестра: браузер передаёт выбор и имя, сервер назначает состав и состояние."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pdrd_api_gateway.transport.http.schemas.experience import ExampleReference


class CreateVersion(BaseModel):
    """Не принимает статус, manifest, авторство, коллекцию или метрики."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["vector", "fine_tune"]
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    items: list[ExampleReference] = Field(min_length=1, max_length=1000)


class ChangeVersion(BaseModel):
    """Действие относится к явно загруженной редакции версии."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    name: str = Field(default="", max_length=200)
