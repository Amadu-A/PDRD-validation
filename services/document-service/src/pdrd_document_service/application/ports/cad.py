# services/document-service/src/pdrd_document_service/application/ports/cad.py

"""Application port подготовки CAD."""

from typing import Protocol

from pdrd_document_service.domain.cad import (
    CadCapabilities,
    CadDocument,
)


class CadProcessingError(RuntimeError):
    """Ошибка infrastructure-обработки CAD."""


class DwgConverterUnavailableError(
    CadProcessingError,
):
    """DWG получен, но converter недоступен."""


class CadProcessor(Protocol):
    """Контракт CAD infrastructure adapter."""

    def capabilities(self) -> CadCapabilities:
        """Возвращает доступные CAD capabilities."""
        ...

    def process(
        self,
        content: bytes,
        *,
        filename: str,
    ) -> CadDocument:
        """Нормализует, анализирует и рендерит CAD."""
        ...
