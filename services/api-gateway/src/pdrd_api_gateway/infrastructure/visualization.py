# services/api-gateway/src/pdrd_api_gateway/infrastructure/visualization.py

"""HTTP adapters lazy-визуализации анализа."""

import base64
import binascii
from typing import Any

import httpx

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisFindingTarget,
    AnalysisPagePreview,
    AnalysisTextWord,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.core.settings import (
    AnalysisServiceSettings,
    DocumentServiceSettings,
)


class DocumentServiceAnalysisPdfPageRenderer:
    """Рендерит selected PDF pages через Document Service."""

    def __init__(
        self,
        *,
        settings: DocumentServiceSettings,
    ) -> None:
        """Сохраняет bounded HTTP settings."""
        self._base_url = settings.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds

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
        """Рендерит selected pages одним HTTP request."""
        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=(self._connect_timeout_seconds),
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/pdf/extract"),
                    files={
                        "file": (
                            file_name,
                            pdf_content,
                            "application/pdf",
                        ),
                    },
                    data={
                        "pages": (page_spec or ""),
                        "use_explanatory_note": ("false"),
                        "include_text_geometry": ("true"),
                    },
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise RuntimeError(
                "Document Service не подготовил PDF-preview.",
            ) from error

        try:
            payload = response.json()

            raw_pages = payload["pages"]

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise RuntimeError(
                "Document Service вернул некорректный preview payload.",
            ) from error

        if not isinstance(
            raw_pages,
            list,
        ):
            raise RuntimeError(
                "Document Service preview pages должен быть массивом.",
            )

        pages: list[AnalysisPagePreview] = []

        for raw_page in raw_pages:
            if not isinstance(
                raw_page,
                dict,
            ):
                raise RuntimeError(
                    "Document Service вернул некорректную страницу preview.",
                )

            image_base64 = str(
                raw_page.get(
                    "image_base64",
                    "",
                )
            ).strip()

            try:
                decoded = base64.b64decode(
                    image_base64,
                    validate=True,
                )

            except (
                binascii.Error,
                ValueError,
            ) as error:
                raise RuntimeError(
                    "Document Service вернул некорректный PNG Base64.",
                ) from error

            if not decoded.startswith(
                b"\x89PNG\r\n\x1a\n",
            ):
                raise RuntimeError(
                    "Document Service preview не является PNG.",
                )

            pages.append(
                AnalysisPagePreview(
                    page_number=int(
                        raw_page["page_number"],
                    ),
                    width_points=float(
                        raw_page["width_points"],
                    ),
                    height_points=float(
                        raw_page["height_points"],
                    ),
                    image_base64=(image_base64),
                    extracted_text=str(
                        raw_page.get(
                            "text",
                            "",
                        )
                    ),
                    text_words=(
                        self._parse_text_words(
                            raw_page.get(
                                "text_words",
                                [],
                            ),
                        )
                    ),
                )
            )

        return tuple(
            pages,
        )

    @staticmethod
    def _parse_text_words(
        raw_words: Any,
    ) -> tuple[
        AnalysisTextWord,
        ...,
    ]:
        """Разбирает optional PDF text geometry."""
        if not isinstance(
            raw_words,
            list,
        ):
            return ()

        result: list[AnalysisTextWord] = []

        for raw_word in raw_words:
            if not isinstance(
                raw_word,
                dict,
            ):
                continue

            text = str(
                raw_word.get(
                    "text",
                    "",
                )
            ).strip()

            raw_bbox = raw_word.get(
                "bbox",
            )

            if not text or not isinstance(
                raw_bbox,
                dict,
            ):
                continue

            try:
                result.append(
                    AnalysisTextWord(
                        text=text,
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
                        block_no=int(
                            raw_word.get(
                                "block_no",
                                0,
                            )
                        ),
                        line_no=int(
                            raw_word.get(
                                "line_no",
                                0,
                            )
                        ),
                        word_no=int(
                            raw_word.get(
                                "word_no",
                                0,
                            )
                        ),
                    )
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

        return tuple(
            result,
        )


class HttpAnalysisFindingLocator:
    """Вызывает VLM fallback localization Analysis Service."""

    def __init__(
        self,
        *,
        settings: AnalysisServiceSettings,
    ) -> None:
        """Сохраняет internal Analysis Service settings."""
        self._base_url = settings.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds

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
        """Локализует unresolved findings одним HTTP request."""
        if not findings:
            return ()

        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=(self._connect_timeout_seconds),
        )

        payload = {
            "page_number": page_number,
            "extracted_text": extracted_text,
            "image_base64": image_base64,
            "findings": [
                {
                    "finding_id": (finding.finding_id),
                    "comment": (finding.comment),
                    "evidence": (finding.evidence),
                }
                for finding in findings
            ],
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/findings/localize"),
                    json=payload,
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise RuntimeError(
                "Analysis Service не выполнил visual localization.",
            ) from error

        try:
            response_payload = response.json()

            raw_locations = response_payload["locations"]

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise RuntimeError(
                "Analysis Service вернул некорректный localization payload.",
            ) from error

        if not isinstance(
            raw_locations,
            list,
        ):
            raise RuntimeError(
                "Localization locations должен быть массивом.",
            )

        allowed_ids = {finding.finding_id for finding in findings}

        parsed: dict[
            str,
            AnalysisFindingLocation,
        ] = {}

        for raw_location in raw_locations:
            location = self._parse_location(
                raw_location=raw_location,
                allowed_ids=allowed_ids,
            )

            if location is None or location.finding_id in parsed:
                continue

            parsed[location.finding_id] = location

        return tuple(
            parsed.get(
                finding.finding_id,
                AnalysisFindingLocation.unlocated(
                    finding_id=(finding.finding_id),
                ),
            )
            for finding in findings
        )

    @staticmethod
    def _parse_location(
        *,
        raw_location: Any,
        allowed_ids: set[str],
    ) -> AnalysisFindingLocation | None:
        """Проверяет один VLM localization item."""
        if not isinstance(
            raw_location,
            dict,
        ):
            return None

        finding_id = str(
            raw_location.get(
                "finding_id",
                "",
            )
        ).strip()

        if finding_id not in allowed_ids:
            return None

        if (
            str(
                raw_location.get(
                    "status",
                    "",
                )
            )
            != "located"
        ):
            return AnalysisFindingLocation.unlocated(
                finding_id=finding_id,
            )

        raw_bbox = raw_location.get(
            "bbox",
        )

        if not isinstance(
            raw_bbox,
            dict,
        ):
            return AnalysisFindingLocation.unlocated(
                finding_id=finding_id,
            )

        try:
            bbox = AnalysisBoundingBox(
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
            )

            confidence = float(
                raw_location.get(
                    "confidence",
                    0.0,
                )
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            return AnalysisFindingLocation.unlocated(
                finding_id=finding_id,
            )

        confidence = min(
            max(
                confidence,
                0.0,
            ),
            1.0,
        )

        region = AnalysisVisualRegion(
            bbox=bbox,
            source="vlm",
            confidence=confidence,
            label=None,
        )

        return AnalysisFindingLocation.located(
            finding_id=finding_id,
            regions=(region,),
            confidence=confidence,
            method="vlm",
        )
