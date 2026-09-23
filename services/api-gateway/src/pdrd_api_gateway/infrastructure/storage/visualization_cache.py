# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/visualization_cache.py

"""Filesystem cache resolved finding locations и annotated PDF."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationCacheError,
    AnalysisVisualizationLocationPage,
)


class LocalFilesystemAnalysisVisualizationCache:
    """Хранит derivative artifacts рядом с analysis document."""

    _VISUALIZATION_DIRECTORY = "visualization"

    _LOCATIONS_FILE = "locations.json"

    _ANNOTATED_PDF_FILE = "annotated-v4.pdf"

    _LOCATIONS_SCHEMA_VERSION = 3

    def __init__(
        self,
        *,
        root_path: Path,
    ) -> None:
        """Сохраняет analysis root."""
        self._root_path = root_path

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
        """Читает cached locations без блокировки event loop."""
        return await asyncio.to_thread(
            self._load_locations_sync,
            document_id,
        )

    async def save_locations(
        self,
        *,
        document_id: UUID,
        pages: tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ],
    ) -> None:
        """Атомарно сохраняет cached locations."""
        await asyncio.to_thread(
            self._save_locations_sync,
            document_id,
            pages,
        )

    async def load_annotated_pdf(
        self,
        *,
        document_id: UUID,
    ) -> bytes | None:
        """Читает cached PDF."""
        return await asyncio.to_thread(
            self._load_annotated_pdf_sync,
            document_id,
        )

    async def save_annotated_pdf(
        self,
        *,
        document_id: UUID,
        content: bytes,
    ) -> None:
        """Атомарно сохраняет cached PDF."""
        await asyncio.to_thread(
            self._save_annotated_pdf_sync,
            document_id,
            content,
        )

    def _load_locations_sync(
        self,
        document_id: UUID,
    ) -> (
        tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ]
        | None
    ):
        path = (
            self._visualization_directory(
                document_id,
            )
            / self._LOCATIONS_FILE
        )

        if not path.is_file():
            return None

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8",
                )
            )

            if not isinstance(
                payload,
                dict,
            ):
                raise ValueError(
                    "Location cache должен быть JSON object.",
                )

            if (
                payload.get(
                    "schema_version",
                )
                != self._LOCATIONS_SCHEMA_VERSION
            ):
                raise ValueError(
                    "Unsupported location cache schema.",
                )

            raw_pages = payload.get(
                "pages",
            )

            if not isinstance(
                raw_pages,
                list,
            ):
                raise ValueError(
                    "Location cache pages должен быть array.",
                )

            pages: list[AnalysisVisualizationLocationPage] = []

            seen_pages: set[int] = set()

            for raw_page in raw_pages:
                if not isinstance(
                    raw_page,
                    dict,
                ):
                    raise ValueError(
                        "Location cache page должен быть object.",
                    )

                page_number = int(raw_page["page_number"])

                if page_number < 1 or page_number in seen_pages:
                    raise ValueError(
                        "Некорректный cached page_number.",
                    )

                seen_pages.add(
                    page_number,
                )

                raw_locations = raw_page.get(
                    "locations",
                )

                if not isinstance(
                    raw_locations,
                    list,
                ):
                    raise ValueError(
                        "Cached locations должен быть array.",
                    )

                locations = tuple(
                    self._parse_location(
                        raw_location,
                    )
                    for raw_location in raw_locations
                )

                pages.append(
                    AnalysisVisualizationLocationPage(
                        page_number=page_number,
                        locations=locations,
                    )
                )

            return tuple(
                pages,
            )

        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise AnalysisVisualizationCacheError(
                "Не удалось прочитать finding location cache.",
            ) from error

    def _save_locations_sync(
        self,
        document_id: UUID,
        pages: tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ],
    ) -> None:
        document_directory = self._document_directory(
            document_id,
        )

        if not document_directory.is_dir():
            raise AnalysisVisualizationCacheError(
                "Analysis artifacts для location cache не найдены.",
            )

        target_directory = self._visualization_directory(
            document_id,
        )

        try:
            target_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            payload = {
                "schema_version": (self._LOCATIONS_SCHEMA_VERSION),
                "pages": [
                    {
                        "page_number": page.page_number,
                        "locations": [
                            location.as_dict() for location in page.locations
                        ],
                    }
                    for page in pages
                ],
            }

            self._write_json_atomic(
                target_directory / self._LOCATIONS_FILE,
                payload,
            )

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise AnalysisVisualizationCacheError(
                "Не удалось сохранить finding location cache.",
            ) from error

    def _load_annotated_pdf_sync(
        self,
        document_id: UUID,
    ) -> bytes | None:
        path = (
            self._document_directory(
                document_id,
            )
            / self._ANNOTATED_PDF_FILE
        )

        if not path.is_file():
            return None

        try:
            content = path.read_bytes()

        except OSError as error:
            raise AnalysisVisualizationCacheError(
                "Не удалось прочитать cached annotated PDF.",
            ) from error

        if not content.startswith(
            b"%PDF-",
        ):
            raise AnalysisVisualizationCacheError(
                "Cached annotated artifact повреждён.",
            )

        return content

    def _save_annotated_pdf_sync(
        self,
        document_id: UUID,
        content: bytes,
    ) -> None:
        document_directory = self._document_directory(
            document_id,
        )

        if not document_directory.is_dir():
            raise AnalysisVisualizationCacheError(
                "Analysis artifacts для annotated PDF не найдены.",
            )

        if not content.startswith(
            b"%PDF-",
        ):
            raise AnalysisVisualizationCacheError(
                "Нельзя сохранить не-PDF annotated artifact.",
            )

        try:
            self._write_bytes_atomic(
                document_directory / self._ANNOTATED_PDF_FILE,
                content,
            )

        except OSError as error:
            raise AnalysisVisualizationCacheError(
                "Не удалось сохранить annotated PDF.",
            ) from error

    @classmethod
    def _parse_location(
        cls,
        raw_location: object,
    ) -> AnalysisFindingLocation:
        """Восстанавливает typed location из JSON."""
        if not isinstance(
            raw_location,
            dict,
        ):
            raise ValueError(
                "Cached location должен быть object.",
            )

        finding_id = str(
            raw_location.get(
                "finding_id",
                "",
            )
        ).strip()

        if not finding_id:
            raise ValueError(
                "Cached location не содержит finding_id.",
            )

        if (
            raw_location.get(
                "status",
            )
            != "located"
        ):
            return AnalysisFindingLocation.unlocated(
                finding_id=finding_id,
            )

        raw_regions = raw_location.get(
            "regions",
        )

        if (
            not isinstance(
                raw_regions,
                list,
            )
            or not raw_regions
        ):
            raise ValueError(
                "Located cache item не содержит regions.",
            )

        regions = tuple(
            cls._parse_region(
                raw_region,
            )
            for raw_region in raw_regions
        )

        return AnalysisFindingLocation.located(
            finding_id=finding_id,
            regions=regions,
            confidence=float(
                raw_location.get(
                    "confidence",
                    0.0,
                )
            ),
            method=str(
                raw_location.get(
                    "method",
                    "cached",
                )
            ),
        )

    @staticmethod
    def _parse_region(
        raw_region: object,
    ) -> AnalysisVisualRegion:
        """Восстанавливает одну visual region."""
        if not isinstance(
            raw_region,
            dict,
        ):
            raise ValueError(
                "Cached region должен быть object.",
            )

        raw_bbox = raw_region.get(
            "bbox",
        )

        if not isinstance(
            raw_bbox,
            dict,
        ):
            raise ValueError(
                "Cached region не содержит bbox.",
            )

        return AnalysisVisualRegion(
            bbox=AnalysisBoundingBox(
                x_min=int(
                    raw_bbox["x_min"],
                ),
                y_min=int(
                    raw_bbox["y_min"],
                ),
                x_max=int(
                    raw_bbox["x_max"],
                ),
                y_max=int(
                    raw_bbox["y_max"],
                ),
            ),
            source=str(
                raw_region.get(
                    "source",
                    "",
                )
            ),
            confidence=float(
                raw_region.get(
                    "confidence",
                    0.0,
                )
            ),
            label=(
                str(raw_region["label"])
                if raw_region.get(
                    "label",
                )
                is not None
                else None
            ),
        )

    def _document_directory(
        self,
        document_id: UUID,
    ) -> Path:
        """Возвращает каталог analysis document."""
        return self._root_path / str(
            document_id,
        )

    def _visualization_directory(
        self,
        document_id: UUID,
    ) -> Path:
        """Возвращает derivative visualization directory."""
        return (
            self._document_directory(
                document_id,
            )
            / self._VISUALIZATION_DIRECTORY
        )

    @staticmethod
    def _write_json_atomic(
        path: Path,
        payload: dict[
            str,
            Any,
        ],
    ) -> None:
        """Атомарно пишет JSON."""
        temporary_path = path.with_suffix(
            f"{path.suffix}.tmp",
        )

        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
            ),
            encoding="utf-8",
        )

        temporary_path.replace(
            path,
        )

    @staticmethod
    def _write_bytes_atomic(
        path: Path,
        content: bytes,
    ) -> None:
        """Атомарно пишет bytes."""
        temporary_path = path.with_suffix(
            f"{path.suffix}.tmp",
        )

        temporary_path.write_bytes(
            content,
        )

        temporary_path.replace(
            path,
        )
