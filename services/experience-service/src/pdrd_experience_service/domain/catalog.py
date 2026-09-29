# services/experience-service/src/pdrd_experience_service/domain/catalog.py

"""Постоянный пример опыта: неизменяемый источник и отдельная инженерная редакция.

Идентичность, решение Review, первоначальный текст и подтверждённая геометрия
не меняются при правке каталога. Неоднозначное отклонение требует разметки.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from pdrd_experience_service.domain.experience_selection import ExperienceCandidate
from pdrd_experience_service.domain.review import Decision, Origin, ReviewError


@dataclass(frozen=True, slots=True)
class Crop:
    """Ссылка на PNG своей области; целый лист в Experience не хранится."""

    sha256: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Example:
    """Версионируемая редакция подтверждённого примера с полным происхождением."""

    id: UUID
    source: ExperienceCandidate
    crops: tuple[Crop, ...]
    revision: int
    document_title: str
    text: str
    normative_basis: str
    normative_reference: str
    active: bool
    rejection_reason: str
    negative_target: str
    created_at: datetime
    updated_at: datetime
    curated_by: str

    @property
    def tag(self) -> str:
        """Ручной пример остаётся Gold, исправление VLM остаётся Edited."""
        if self.source.origin is Origin.MANUAL:
            return "gold"
        if (
            self.source.tag == "edited"
            or self.text != self.source.text
            or self.normative_basis != self.source.normative_basis
        ):
            return "edited"
        return self.source.tag

    @property
    def learning_use(self) -> str:
        """Явная разметка разрешает отрицательный пример после неоднозначной правки."""
        if self.source.decision is Decision.ACCEPTED:
            return "positive"
        if self.tag == "edited" and not (
            self.rejection_reason and self.negative_target
        ):
            return "needs_adjudication"
        return "negative"

    def curate(self, *, fields: dict, actor: str, at: datetime) -> "Example":
        """Проверяет инженерную редакцию, сохраняя доверенный source целиком."""
        allowed = {
            "document_title",
            "text",
            "normative_basis",
            "normative_reference",
            "active",
            "rejection_reason",
            "negative_target",
        }
        if not fields or not fields.keys() <= allowed:
            raise ReviewError("Можно менять только инженерные поля каталога.")
        changes = {}
        for name, value in fields.items():
            if name == "active":
                if not isinstance(value, bool):
                    raise ReviewError("Активность должна быть указана явно.")
                changes[name] = value
                continue
            if not isinstance(value, str):
                raise ReviewError("Текстовые поля должны быть строками.")
            changes[name] = value.strip()
        text_changed = (
            changes.get("text", self.text) != self.text
            or changes.get("normative_basis", self.normative_basis)
            != self.normative_basis
        )
        if (
            text_changed
            and self.negative_target in {"revised", "both"}
            and not {"rejection_reason", "negative_target"} <= fields.keys()
        ):
            # Оценка прежней исправленной формулировки не подтверждает новую.
            changes.update(rejection_reason="", negative_target="")
        result = replace(
            self, **changes, revision=self.revision + 1, updated_at=at, curated_by=actor
        )
        limits = {
            "document_title": 500,
            "text": 10000,
            "normative_basis": 2000,
            "normative_reference": 2000,
            "rejection_reason": 1000,
        }
        if (
            not result.text
            or not result.document_title
            or any(len(getattr(result, key)) > limit for key, limit in limits.items())
        ):
            raise ReviewError("Проверьте длину и заполнение полей каталога.")
        if result.negative_target not in {"", "original", "revised", "both"}:
            raise ReviewError("Укажите объект отрицательного примера.")
        if bool(result.rejection_reason) != bool(result.negative_target):
            raise ReviewError("Причина отказа и объект разметки задаются вместе.")
        if result.source.decision is not Decision.REJECTED and (
            result.rejection_reason or result.negative_target
        ):
            raise ReviewError(
                "Причину отклонения можно задать только отклонённому примеру."
            )
        if not actor.strip() or at.tzinfo is None:
            raise ReviewError("Отсутствует серверный контекст редакции.")
        return result


@dataclass(frozen=True, slots=True)
class CatalogFilter:
    """Серверная фильтрация с ограниченной страницей и стабильным порядком."""

    query: str = ""
    tag: str = ""
    decision: str = ""
    active: bool | None = None
    learning_use: str = ""
    job_id: UUID | None = None
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Состояние источника вычисляет сервер, а не сохранённый флаг браузера."""

    example: Example
    source_current: bool

    @property
    def active(self) -> bool:
        """Отозванный источник исключает пример из автоматического использования."""
        return self.example.active and self.source_current
