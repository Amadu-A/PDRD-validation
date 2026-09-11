# services/api-gateway/src/pdrd_api_gateway/infrastructure/visualization.py

"""HTTP adapter повторного рендера selected PDF pages через Document Service."""

import base64
import binascii

import httpx

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisPagePreview,
)


class DocumentServiceAnalysisPdfPageRenderer:
    """Использует существующий /internal/v1/pdf/extract."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
    ) -> None:
        """Сохраняет bounded HTTP settings."""
        self._base_url = base_url.rstrip(
            "/",
        )
        self._request_timeout_seconds = request_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds

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
        """Рендерит selected pages ровно одним internal HTTP-вызовом."""
        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=self._connect_timeout_seconds,
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
                        "pages": page_spec or "",
                        "use_explanatory_note": "false",
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
                    image_base64=image_base64,
                )
            )

        return tuple(
            pages,
        )
