# services/document-service/src/pdrd_document_service/infrastructure/pdf/crop.py

"""PyMuPDF вырезает видимый лист через DisplayList с учётом rotation/cropbox.

clip задаётся в системе отображённой страницы. Отрисовывается только область,
поэтому большой инженерный лист не превращается в гигантское изображение.
"""

from dataclasses import dataclass

import fitz

from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox


@dataclass(frozen=True, slots=True)
class PyMuPdfCropRenderer:
    """Самостоятельный PNG без карточек, соединителей и чужих PDF-аннотаций."""

    max_side: int = 2000

    def render(
        self,
        *,
        content: bytes,
        page_number: int,
        regions: tuple[PdfNormalizedBoundingBox, ...],
    ) -> tuple[bytes, ...]:
        """Не меняет PDF; ограничивает размеры изображения каждой области."""
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.is_encrypted or not 1 <= page_number <= len(document):
                    raise ValueError("Лист недоступен для вырезания области.")
                page = document[page_number - 1]
                display = page.get_displaylist(annots=False)
                bounds = display.rect
                images = []
                for region in regions:
                    clip = fitz.Rect(
                        bounds.x0 + bounds.width * region.x_min / 1000,
                        bounds.y0 + bounds.height * region.y_min / 1000,
                        bounds.x0 + bounds.width * region.x_max / 1000,
                        bounds.y0 + bounds.height * region.y_max / 1000,
                    )
                    scale = min(4.0, (self.max_side - 2) / max(clip.width, clip.height))
                    pixmap = display.get_pixmap(
                        matrix=fitz.Matrix(scale, scale),
                        clip=clip,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    if not 1 <= max(pixmap.width, pixmap.height) <= self.max_side:
                        raise ValueError("Размер crop превышает допустимый предел.")
                    images.append(pixmap.tobytes("png"))
                return tuple(images)
        except (fitz.FileDataError, RuntimeError) as error:
            raise ValueError("Не удалось вырезать область исходного PDF.") from error
