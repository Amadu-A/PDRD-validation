# services/experience-service/src/pdrd_experience_service/transport/http/schemas/artifact_quality.py

"""Отчёт оценки принимается отдельно от сборки; служебный worker не выдаёт допуск."""

from pydantic import BaseModel, ConfigDict, Field


class QualityRevision(BaseModel):
    """Ожидаемая редакция защищает загрузку и отзыв из устаревшей вкладки."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


class RegisterQuality(QualityRevision):
    """Структуру и метрики проверяет доменное правило владельца Experience."""

    report: dict
