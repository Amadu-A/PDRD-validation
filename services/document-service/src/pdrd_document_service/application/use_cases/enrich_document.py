# services/document-service/src/pdrd_document_service/application/use_cases/enrich_document.py

"""Выборочная конверсия сканов и сложных таблиц без повторного OCR надёжного текста."""

import asyncio
import logging
from dataclasses import dataclass, replace

from pdrd_document_service.application.ports.document_conversion import (
    DocumentConversionError,
    DocumentConverter,
)
from pdrd_document_service.domain.pdf import PdfDocument, PdfPageType
from pdrd_document_service.domain.project_context import ExplanatoryNoteContext

logger = logging.getLogger(__name__)


def reuse_converted_project_context(
    document: PdfDocument, context: ExplanatoryNoteContext
) -> ExplanatoryNoteContext:
    """Передаёт уже прочитанные сканы в пустые страницы ПЗ без нового OCR.

    Физические номера и маркер альтернативного чтения сохраняются. Надёжный
    нативный текст не заменяется; отсутствующие в анализе страницы не достраиваются.
    """
    if not context.enabled:
        return context
    converted = {
        page.number: page.text
        for page in document.pages
        if page.text.strip()
        and (page.conversion_metadata or {}).get("provider") == "docling"
        and page.conversion_metadata.get("status") == "ready"
        and page.conversion_metadata.get("do_ocr") is True
    }
    if not converted:
        return context
    return replace(
        context,
        pages=tuple(
            replace(page, text=converted[page.number])
            if not page.text.strip() and page.number in converted
            else page
            for page in context.pages
        ),
    )


def _converted_text(content: dict) -> str:
    """Сохраняет текст и все непустые ячейки с индексами, без вывода о достоверности OCR."""
    parts = [
        item["text"]
        for item in content.get("texts", [])
        if isinstance(item.get("text"), str)
    ]
    for number, table in enumerate(content.get("tables", []), 1):
        cells = table.get("data", {}).get("table_cells", [])
        rows = [
            f"row={cell.get('start_row_offset_idx')} col={cell.get('start_col_offset_idx')} "
            f"row_end={cell.get('end_row_offset_idx')} col_end={cell.get('end_col_offset_idx')}: {cell['text']}"
            for cell in cells
            if isinstance(cell.get("text"), str) and cell["text"].strip()
        ]
        if rows:
            parts.append(f"Таблица Docling {number}\n" + "\n".join(rows))
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class EnrichDocument:
    """Сохраняет исходный текст, альтернативное чтение и полный артефакт Docling."""

    converter: DocumentConverter
    concurrency: int = 2

    async def execute(self, *, document: PdfDocument, content: bytes) -> PdfDocument:
        """Не отправляет в Docling уже разобранную спецификацию и хорошие текстовые листы."""
        semaphore = asyncio.Semaphore(self.concurrency)

        async def convert(page):
            """Обрабатывает только обоснованно выбранный проблемный физический лист."""
            specification = page.equipment_specification or {}
            external = (
                specification.get("external_extraction", {}).get("provider")
                == "spec_extracting_ai"
            )
            scanned = not page.text.strip() and page.page_type not in {
                PdfPageType.DRAWING,
                PdfPageType.SCHEME,
            }
            complex_table = (
                page.page_type in {PdfPageType.TABLE, PdfPageType.SPECIFICATION}
                and specification.get("status") != "reliable"
            )
            if external or not (scanned or complex_table):
                return page
            async with semaphore:
                try:
                    converted = await self.converter.convert(
                        content=content, page_number=page.number, do_ocr=scanned
                    )
                except DocumentConversionError as error:
                    logger.warning(
                        "document_convert operation=docling status=fallback page=%s error_type=%s",
                        page.number,
                        type(error).__name__,
                    )
                    return replace(
                        page,
                        conversion_metadata={
                            "provider": "docling",
                            "status": "fallback",
                            "reason": type(error).__name__,
                        },
                    )
                alternative = _converted_text(
                    converted["result"]["document"]["json_content"]
                )
                # Не дублируем полный native/alternative в нескольких полях n8n.
                # Полный JSON, bbox и исходные ячейки уже сохранены адаптером.
                text = page.text
                if alternative and alternative != page.text:
                    marker = (
                        "DOCLING_OCR_ALTERNATIVE" if scanned else "DOCLING_ALTERNATIVE"
                    )
                    text += (
                        f"\n[{marker}: чтение требует сопоставления с исходным PDF]\n"
                        + alternative
                    )
                return replace(
                    page,
                    text=text,
                    conversion_metadata={
                        "provider": "docling",
                        "status": "ready",
                        "native_text_preserved": not scanned,
                        "artifact_key": converted["artifact_key"],
                        "physical_page": page.number,
                        "do_ocr": scanned,
                    },
                )

        return replace(
            document,
            pages=tuple(
                await asyncio.gather(*(convert(page) for page in document.pages))
            ),
        )
