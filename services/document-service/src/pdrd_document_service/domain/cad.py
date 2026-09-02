# services/document-service/src/pdrd_document_service/domain/cad.py

"""Domain-модели CAD-документов."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class CadFormat(StrEnum):
    """Поддерживаемые исходные CAD-форматы."""

    DXF = "dxf"
    DWG = "dwg"


class InvalidCadFilenameError(ValueError):
    """Ошибка неподдерживаемого или отсутствующего CAD filename."""


@dataclass(frozen=True, slots=True)
class CadCapabilities:
    """Доступные возможности CAD processor."""

    dxf: bool
    dwg: bool
    dwg_converter: str | None
    dwg_converter_command: str


@dataclass(frozen=True, slots=True)
class CadDocument:
    """Результат подготовки одного CAD-листа."""

    original_file_name: str
    original_format: CadFormat
    normalized_format: CadFormat

    converted_from_dwg: bool
    selected_layout: str

    warnings: tuple[str, ...]

    machine_data: dict[str, Any]
    machine_context: str

    rendered_png: bytes


def detect_cad_format(
    filename: str | None,
) -> CadFormat:
    """Определяет CAD-формат по имени файла."""
    if not filename:
        raise InvalidCadFilenameError(
            "У CAD-файла отсутствует имя.",
        )

    extension = Path(
        filename,
    ).suffix.lower()

    try:
        return CadFormat(
            extension.lstrip("."),
        )
    except ValueError as error:
        raise InvalidCadFilenameError(
            "Поддерживаются только DWG и DXF. "
            f"Получено расширение: {extension or '<нет>'}.",
        ) from error
