# services/document-service/src/pdrd_document_service/application/ports/image_composer.py

"""Application port объединения рендеров документов."""

from typing import Protocol


class ImageCompositionError(RuntimeError):
    """Ошибка infrastructure-композиции изображений."""


class CombinedImageComposer(Protocol):
    """Контракт объединения PDF и CAD рендеров."""

    def compose(
        self,
        *,
        pdf_png: bytes,
        cad_png: bytes,
    ) -> bytes:
        """Возвращает единый PNG для PDF + CAD анализа."""
        ...
