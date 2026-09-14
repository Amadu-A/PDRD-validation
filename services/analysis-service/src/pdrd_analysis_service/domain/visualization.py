# services/analysis-service/src/pdrd_analysis_service/domain/visualization.py

"""Domain-модели локализации итоговых замечаний на изображении листа."""

from dataclasses import dataclass
from typing import Literal

FindingLocationStatus = Literal[
    "located",
    "unlocated",
]


@dataclass(frozen=True, slots=True)
class NormalizedBoundingBox:
    """BBox в нормализованных координатах 0..1000."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    def __post_init__(
        self,
    ) -> None:
        """Проверяет границы и положительную площадь."""
        coordinates = (
            self.x_min,
            self.y_min,
            self.x_max,
            self.y_max,
        )

        if any(coordinate < 0 or coordinate > 1000 for coordinate in coordinates):
            raise ValueError(
                "Координаты bbox должны находиться в диапазоне 0..1000.",
            )

        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(
                "BBox должен иметь положительную ширину и высоту.",
            )

    def as_dict(
        self,
    ) -> dict[str, int]:
        """Возвращает transport-friendly представление."""
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }


@dataclass(frozen=True, slots=True)
class FindingLocalizationTarget:
    """Минимальные данные финального finding для визуальной локализации."""

    finding_id: str
    comment: str
    evidence: str


@dataclass(frozen=True, slots=True)
class FindingLocation:
    """Результат локализации одного finding."""

    finding_id: str
    status: FindingLocationStatus
    bbox: NormalizedBoundingBox | None
    confidence: float

    @classmethod
    def unlocated(
        cls,
        *,
        finding_id: str,
    ) -> "FindingLocation":
        """Создаёт безопасный fallback без придуманного bbox."""
        return cls(
            finding_id=finding_id,
            status="unlocated",
            bbox=None,
            confidence=0.0,
        )

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-ready payload."""
        return {
            "finding_id": self.finding_id,
            "status": self.status,
            "bbox": (self.bbox.as_dict() if self.bbox is not None else None),
            "confidence": self.confidence,
        }
