# services/document-service/src/pdrd_document_service/domain/equipment_document.py

"""Текстовые страницы сохранённого технического документа производителя."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EquipmentDocumentPage:
    """Физическая страница и дословный извлечённый текст."""

    page: int
    text: str


@dataclass(frozen=True, slots=True)
class EquipmentDocumentText:
    """Ограниченное текстовое представление PDF или HTML без активного содержимого."""

    media_type: str
    total_pages: int
    pages: tuple[EquipmentDocumentPage, ...]
