# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/normative_requirements.py

"""Ограниченные команды аттестации нормативного статуса; служебные поля задаёт сервер."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class RecordNormativeStatusRequest(BaseModel):
    """Оператор прикладывает проверяемое свидетельство; автор, SHA и время не принимаются от клиента."""

    model_config = ConfigDict(extra="forbid")
    document_code: str = Field(min_length=3, max_length=160)
    edition: str = Field(min_length=1, max_length=120)
    edition_status: Literal["current", "withdrawn", "unknown", "disputed"]
    requirement_binding: Literal["mandatory", "voluntary", "unknown", "disputed"]
    clause_scope: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=300
    )
    applicability_scope: str = Field(min_length=1, max_length=4000)
    source_url: str = Field(min_length=8, max_length=2048)
    source_quote: str = Field(min_length=1, max_length=16000)
    confirmed_on: date
    valid_from: date
    valid_until: date
