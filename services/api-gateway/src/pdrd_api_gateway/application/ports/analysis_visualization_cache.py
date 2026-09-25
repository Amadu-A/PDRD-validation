# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_visualization_cache.py

"""Контракты хранения производных материалов визуализации и экспорта."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisFindingLocation,
)


class AnalysisVisualizationCacheError(
    RuntimeError,
):
    """Ошибка чтения или записи кэша визуализации."""


@dataclass(frozen=True, slots=True)
class AnalysisVisualizationLocationPage:
    """Координаты одной физической страницы и подпись входных замечаний."""

    page_number: int

    locations: tuple[
        AnalysisFindingLocation,
        ...,
    ]

    source_signature: str | None = None


class AnalysisVisualizationCache(
    Protocol,
):
    """Хранилище location cache и готового annotated PDF."""

    async def load_locations(
        self,
        *,
        document_id: UUID,
    ) -> (
        tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ]
        | None
    ):
        """Возвращает cached locations либо None."""
        ...

    async def save_locations(
        self,
        *,
        document_id: UUID,
        pages: tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ],
    ) -> None:
        """Атомарно сохраняет resolved locations."""
        ...

    async def load_annotated_pdf(
        self,
        *,
        document_id: UUID,
    ) -> bytes | None:
        """Возвращает cached annotated PDF."""
        ...

    async def save_annotated_pdf(
        self,
        *,
        document_id: UUID,
        content: bytes,
    ) -> None:
        """Атомарно сохраняет готовый annotated PDF."""
        ...
