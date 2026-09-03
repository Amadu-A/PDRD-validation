# services/knowledge-service/src/pdrd_knowledge_service/application/ports/office_conversion.py

"""Application port нормализации Word в PDF."""

from typing import Protocol


class NormativeOfficeConversionError(
    RuntimeError,
):
    """Ошибка преобразования Word-документа в PDF."""


class NormativeOfficeToPdfConverter(
    Protocol,
):
    """Контракт преобразования Word bytes в PDF bytes."""

    async def convert_to_pdf(
        self,
        *,
        content: bytes,
        original_name: str,
    ) -> bytes:
        """Преобразует DOC/DOCX в PDF."""
        ...
