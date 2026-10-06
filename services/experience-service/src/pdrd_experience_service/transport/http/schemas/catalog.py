# services/experience-service/src/pdrd_experience_service/transport/http/schemas/catalog.py

"""Строгие пользовательские поля каталога: источник, actor и теги не принимаются."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogQuery(BaseModel):
    """Фильтрация всех записей на сервере с ограниченной страницей."""

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
    author: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=50, ge=1, le=100)


class CuratedFields(BaseModel):
    """Отсутствующие поля остаются прежними; null и неизвестные поля запрещены."""

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
    """CAS каждой выбранной строки; UUID разбирается отдельно от strict чисел."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=0, strict=True)


class DeleteSelection(BaseModel):
    """Один ограниченный атомарный запрос вместо цикла HTTP DELETE."""

    model_config = ConfigDict(extra="forbid")
    items: list[ExampleReference] = Field(min_length=1, max_length=1000)


class CatalogUpdate(BaseModel):
    """Отдельная CAS-ревизия каталога не изменяет редакцию Review."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    fields: CuratedFields


class CaptureCommand(BaseModel):
    """Клиент сообщает только ожидаемую утверждённую редакцию."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
