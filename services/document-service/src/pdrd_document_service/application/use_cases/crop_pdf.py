# services/document-service/src/pdrd_document_service/application/use_cases/crop_pdf.py

"""Проверяет ограничения запроса перед вырезанием областей исходного PDF."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.pdf_crop import PdfCropRenderer
from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox


@dataclass(frozen=True, slots=True)
class CropPdf:
    """Не определяет, подтверждены ли области: это ответственность вызывающего сервиса."""

    renderer: PdfCropRenderer
    max_upload_bytes: int

    def execute(
        self,
        *,
        content: bytes,
        page_number: int,
        regions: tuple[PdfNormalizedBoundingBox, ...],
    ) -> tuple[bytes, ...]:
        """Не сохраняет лист и ограничивает расход памяти каждого crop."""
        if not content.startswith(b"%PDF-") or len(content) > self.max_upload_bytes:
            raise ValueError("Проверьте формат и размер исходного PDF.")
        if page_number < 1 or not 1 <= len(regions) <= 4:
            raise ValueError("Укажите лист и от одной до четырёх областей.")
        return self.renderer.render(
            content=content, page_number=page_number, regions=regions
        )
