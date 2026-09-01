# services/document-service/src/pdrd_document_service/application/use_cases/combined.py

"""Use case подготовки объединённого PDF + CAD представления."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.image_composer import (
    CombinedImageComposer,
)
from pdrd_document_service.application.use_cases.cad import (
    ExtractCadDocument,
)
from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.domain.combined import (
    CombinedDocument,
    CombinedPageSelectionError,
)


@dataclass(frozen=True, slots=True)
class ExtractCombinedDocument:
    """Подготавливает соответствующие PDF и CAD листы для анализа."""

    extract_pdf: ExtractPdfDocument
    extract_cad: ExtractCadDocument
    image_composer: CombinedImageComposer

    def execute(
        self,
        *,
        pdf_content: bytes,
        cad_content: bytes,
        cad_filename: str,
        page_spec: str | None,
    ) -> CombinedDocument:
        """Извлекает оба источника и строит единое представление."""
        pdf_document = self.extract_pdf.execute(
            content=pdf_content,
            page_spec=page_spec,
        )

        if len(pdf_document.pages) != 1:
            raise CombinedPageSelectionError(
                "Для режима PDF + CAD необходимо выбрать ровно одну PDF-страницу.",
            )

        cad_document = self.extract_cad.execute(
            content=cad_content,
            filename=cad_filename,
        )

        pdf_page = pdf_document.pages[0]

        combined_rendered_png = self.image_composer.compose(
            pdf_png=pdf_page.rendered_png,
            cad_png=cad_document.rendered_png,
        )

        analysis_text = self._build_analysis_text(
            pdf_text=pdf_page.text,
            cad_machine_context=cad_document.machine_context,
        )

        return CombinedDocument(
            pdf=pdf_document,
            cad=cad_document,
            analysis_text=analysis_text,
            combined_rendered_png=combined_rendered_png,
        )

    @staticmethod
    def _build_analysis_text(
        *,
        pdf_text: str,
        cad_machine_context: str,
    ) -> str:
        """Формирует текстовый контекст двух представлений листа."""
        return (
            "[PDF_TEXT]\n"
            f"{pdf_text.strip()}\n\n"
            "[CAD_MACHINE_CONTEXT]\n"
            f"{cad_machine_context.strip()}"
        )
