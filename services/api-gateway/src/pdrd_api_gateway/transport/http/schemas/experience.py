# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/experience.py

"""Внешний контракт каталога; оригиналы, координаты и серверный actor запрещены."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExperienceQuery(BaseModel):
    """Только известные серверные фильтры, ограниченные limit/offset."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=500)
    tag: Literal[
        "",
        "wise",
        "bad",
        "gold",
        "edited",
        "edited:accepted",
        "edited:rejected",
        "edited:pending",
    ] = ""
    decision: Literal["", "accepted", "rejected"] = ""
    active: bool | None = None
    learning_use: Literal["", "positive", "negative", "needs_adjudication"] = ""
    job_id: UUID | None = None
    section_id: str = Field(default="", max_length=128)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=50, ge=1, le=100)


class ExperienceFields(BaseModel):
    """Инженерная редакция отдельна от неизменяемого source."""

    model_config = ConfigDict(extra="forbid", strict=True)
    document_title: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=10000)
    normative_basis: str = Field(default="", max_length=2000)
    normative_reference: str = Field(default="", max_length=2000)
    active: bool = True
    rejection_reason: str = Field(default="", max_length=1000)
    negative_target: Literal["", "original", "revised", "both"] = ""
    section_id: str = Field(default="", max_length=128)
    section_title: str = Field(default="", max_length=300)


class ExampleReference(BaseModel):
    """Сервер сверяет ревизию каждой выбранной строки."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=0, strict=True)


class DeleteSelection(BaseModel):
    """Ограниченный набор для удаления одной транзакцией."""

    model_config = ConfigDict(extra="forbid")
    items: list[ExampleReference] = Field(min_length=1, max_length=1000)


class ExperienceUpdate(BaseModel):
    """CAS-ревизия относится к каталогу, а не к исходному Review."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    fields: ExperienceFields
