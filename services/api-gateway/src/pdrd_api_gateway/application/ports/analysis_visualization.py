# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_visualization.py

"""Application ports lazy-визуализации результата анализа."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AnalysisBoundingBox:
    """Нормализованный bbox в диапазоне 0..1000."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    def __post_init__(
        self,
    ) -> None:
        """Проверяет bbox."""
        coordinates = (
            self.x_min,
            self.y_min,
            self.x_max,
            self.y_max,
        )

        if any(coordinate < 0 or coordinate > 1000 for coordinate in coordinates):
            raise ValueError(
                "BBox coordinates должны быть в диапазоне 0..1000.",
            )

        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(
                "BBox должен иметь положительную площадь.",
            )

    def as_dict(
        self,
    ) -> dict[str, int]:
        """Возвращает JSON-ready bbox."""
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }


@dataclass(frozen=True, slots=True)
class AnalysisFindingTarget:
    """Finding, который требуется найти на странице."""

    finding_id: str
    comment: str
    evidence: str


@dataclass(frozen=True, slots=True)
class AnalysisFindingLocation:
    """Visual location одного finding."""

    finding_id: str
    status: str
    bbox: AnalysisBoundingBox | None
    confidence: float

    @classmethod
    def unlocated(
        cls,
        *,
        finding_id: str,
    ) -> "AnalysisFindingLocation":
        """Создаёт безопасный fallback."""
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
        """Возвращает JSON-ready location."""
        return {
            "finding_id": self.finding_id,
            "status": self.status,
            "bbox": (self.bbox.as_dict() if self.bbox is not None else None),
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class AnalysisPagePreview:
    """Один rendered PDF-preview."""

    page_number: int
    width_points: float
    height_points: float
    image_base64: str
    extracted_text: str

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает frontend payload без дублирования page text."""
        return {
            "page_number": self.page_number,
            "width_points": self.width_points,
            "height_points": self.height_points,
            "image_base64": self.image_base64,
        }


class AnalysisPdfPageRenderer(Protocol):
    """Контракт повторного рендера исходного PDF."""

    async def render(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        page_spec: str | None,
    ) -> tuple[
        AnalysisPagePreview,
        ...,
    ]:
        """Рендерит selected pages."""
        ...


class AnalysisFindingLocator(Protocol):
    """Контракт lazy visual localization."""

    async def localize(
        self,
        *,
        page_number: int,
        extracted_text: str,
        image_base64: str,
        findings: tuple[
            AnalysisFindingTarget,
            ...,
        ],
    ) -> tuple[
        AnalysisFindingLocation,
        ...,
    ]:
        """Локализует findings одним запросом на страницу."""
        ...
