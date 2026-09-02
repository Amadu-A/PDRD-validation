# services/document-service/src/pdrd_document_service/application/use_cases/cad.py

"""Use case подготовки CAD-документа."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.cad import (
    CadProcessor,
)
from pdrd_document_service.domain.cad import (
    CadCapabilities,
    CadDocument,
    detect_cad_format,
)


class EmptyCadError(ValueError):
    """Ошибка пустого CAD payload."""


class CadTooLargeError(ValueError):
    """Ошибка превышения допустимого размера CAD."""


@dataclass(frozen=True, slots=True)
class ExtractCadDocument:
    """Подготавливает DWG/DXF для дальнейшего анализа."""

    processor: CadProcessor
    max_upload_bytes: int

    def capabilities(self) -> CadCapabilities:
        """Возвращает CAD capabilities adapter."""
        return self.processor.capabilities()

    def execute(
        self,
        *,
        content: bytes,
        filename: str,
    ) -> CadDocument:
        """Валидирует запрос и запускает CAD processor."""
        if not content:
            raise EmptyCadError(
                "Передан пустой CAD-файл.",
            )

        if len(content) > self.max_upload_bytes:
            raise CadTooLargeError(
                "Размер CAD-файла превышает допустимый предел.",
            )

        detect_cad_format(
            filename,
        )

        return self.processor.process(
            content,
            filename=filename,
        )
