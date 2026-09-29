# services/document-service/src/pdrd_document_service/application/ports/pdf_crop.py

"""Порт вырезания областей PDF без зависимости от Review или Experience."""

from typing import Protocol

from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox


class PdfCropRenderer(Protocol):
    """Координаты относятся к видимому повёрнутому листу 0..1000."""

    def render(
        self,
        *,
        content: bytes,
        page_number: int,
        regions: tuple[PdfNormalizedBoundingBox, ...],
    ) -> tuple[bytes, ...]:
        """Возвращает самостоятельный PNG каждой заданной области."""
        ...
