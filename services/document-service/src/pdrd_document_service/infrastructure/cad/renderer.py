# services/document-service/src/pdrd_document_service/infrastructure/cad/renderer.py

"""Raster rendering DXF layout."""

import io
from typing import Any

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as drawing_layout
from ezdxf.addons.drawing import pymupdf as drawing_pymupdf
from PIL import Image

from pdrd_document_service.application.ports.cad import (
    CadProcessingError,
)


class EzdxfCadRenderer:
    """Рендерит выбранный DXF layout в PNG."""

    def __init__(
        self,
        *,
        render_dpi: int,
        render_max_side: int,
    ) -> None:
        """Сохраняет параметры raster rendering."""
        self._render_dpi = render_dpi
        self._render_max_side = render_max_side

    def render(
        self,
        *,
        document: ezdxf.document.Drawing,
        selected_layout: Any,
    ) -> bytes:
        """Возвращает PNG выбранного layout."""
        try:
            backend = drawing_pymupdf.PyMuPdfBackend()

            Frontend(
                RenderContext(
                    document,
                ),
                backend,
            ).draw_layout(
                selected_layout,
            )

            image_bytes = backend.get_pixmap_bytes(
                drawing_layout.Page(
                    0,
                    0,
                ),
                fmt="png",
                dpi=self._render_dpi,
            )
        except Exception as error:
            raise CadProcessingError(
                f"Не удалось отрендерить DXF в изображение: {error}",
            ) from error

        try:
            with Image.open(
                io.BytesIO(
                    image_bytes,
                )
            ) as source_image:
                image = source_image.convert(
                    "RGB",
                )
        except Exception as error:
            raise CadProcessingError(
                "Не удалось прочитать raster DXF render.",
            ) from error

        image.thumbnail(
            (
                self._render_max_side,
                self._render_max_side,
            ),
            Image.Resampling.LANCZOS,
        )

        output = io.BytesIO()

        image.save(
            output,
            format="PNG",
            optimize=True,
        )

        return output.getvalue()
