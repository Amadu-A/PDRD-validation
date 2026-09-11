# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_visualization.py

"""Application port подготовки PNG-preview выбранных PDF-страниц."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AnalysisPagePreview:
    """Один готовый для frontend PNG-preview."""

    page_number: int
    width_points: float
    height_points: float
    image_base64: str

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-ready payload."""
        return {
            "page_number": self.page_number,
            "width_points": self.width_points,
            "height_points": self.height_points,
            "image_base64": self.image_base64,
        }


class AnalysisPdfPageRenderer(Protocol):
    """Контракт повторного рендера исходного PDF для визуализации."""

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
        """Рендерит выбранные страницы одним internal HTTP-вызовом."""
        ...
