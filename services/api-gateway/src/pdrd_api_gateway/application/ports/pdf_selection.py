# services/api-gateway/src/pdrd_api_gateway/application/ports/pdf_selection.py

"""Проверка PDF в Document Service до создания долговечного задания."""

from typing import Protocol


class InvalidPdfSelectionError(ValueError):
    """PDF или пользовательский диапазон отклонён до запуска анализа."""


class PdfSelectionUnavailableError(RuntimeError):
    """Проверка PDF временно недоступна; задание не должно уходить в очередь."""


class PdfSelectionValidator(Protocol):
    """Граница проверки страниц без зависимости Gateway от PDF-библиотеки."""

    async def validate(
        self, *, content: bytes, file_name: str, pages: str | None
    ) -> None:
        """Подтверждает выбор либо возвращает понятную ошибку пользователю."""
        ...
