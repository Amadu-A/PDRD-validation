# services/analysis-service/src/pdrd_analysis_service/transport/http/normative_corpus_schemas.py

"""Выбранный нормативный scope одинаков для PDF, CAD и смешанного режима."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NormativeCorpusScope(BaseModel):
    """Закреплённые поколения позволяют повторить проверку того же корпуса."""

    model_config = ConfigDict(extra="forbid")
    section_id: UUID
    document_ids: list[UUID] = Field(max_length=1000)
    generation_ids: dict[UUID, UUID] = Field(default_factory=dict)
    assessed_on: date | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        """Исключает дубли и поколения документов вне выбранного корпуса."""
        if len(set(self.document_ids)) != len(self.document_ids) or set(
            self.generation_ids
        ) - set(self.document_ids):
            raise ValueError(
                "Поколения должны принадлежать уникальным документам выбранного корпуса."
            )
        return self
