# services/document-service/src/pdrd_document_service/application/ports/document_conversion.py

"""Контракт выборочного чтения сложных PDF-страниц существующим Docling Serve."""

from typing import Protocol


class DocumentConversionError(RuntimeError):
    """Конверсия не завершена; исходный PDF и нативные сведения остаются доступными."""


class DocumentConverter(Protocol):
    """Не заменяет надёжный нативный текст и не извлекает состав спецификации повторно."""

    async def convert(self, *, content: bytes, page_number: int, do_ocr: bool) -> dict:
        """Возвращает сохранённый полный результат и ключ артефакта одной физической страницы."""
        ...
