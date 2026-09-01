# services/document-service/src/pdrd_document_service/domain/combined.py

"""Domain-модели объединённого PDF + CAD представления."""

from dataclasses import dataclass

from pdrd_document_service.domain.cad import CadDocument
from pdrd_document_service.domain.pdf import PdfDocument, PdfPage


class CombinedPageSelectionError(ValueError):
    """Ошибка выбора PDF-листа для combined PDF + CAD режима."""


@dataclass(frozen=True, slots=True)
class CombinedDocument:
    """Подготовленное представление соответствующих PDF и CAD листов."""

    pdf: PdfDocument
    cad: CadDocument

    analysis_text: str
    combined_rendered_png: bytes

    @property
    def page(self) -> PdfPage:
        """Возвращает единственный выбранный PDF-лист."""
        return self.pdf.pages[0]
