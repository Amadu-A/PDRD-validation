# services/document-service/src/pdrd_document_service/infrastructure/pdf/equipment_report.py

"""Повторно использует Unicode layout отчёта без вмешательства в Review-геометрию."""

import fitz

from pdrd_document_service.application.ports.pdf_annotation import PdfTextReport
from pdrd_document_service.infrastructure.pdf.annotator import _PdfReportWriter


class PyMuPdfEquipmentReportWriter:
    """Создаёт только новые страницы самостоятельного отчёта EQ."""

    def build(self, report: PdfTextReport) -> bytes:
        """Использует тот же проверенный перенос Unicode-текста и страниц."""
        with fitz.open() as document:
            _PdfReportWriter(document=document, font=fitz.Font("cjk")).append(report)
            return document.tobytes(garbage=3, deflate=True)
