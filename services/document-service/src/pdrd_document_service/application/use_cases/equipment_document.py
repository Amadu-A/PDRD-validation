# services/document-service/src/pdrd_document_service/application/use_cases/equipment_document.py

"""Извлечение текста документа производителя без визуального обхода всех страниц."""

from dataclasses import dataclass
from typing import Protocol

from pdrd_document_service.domain.equipment_document import EquipmentDocumentText


class EquipmentDocumentReader(Protocol):
    """Порт безопасного текстового извлечения PDF/HTML."""

    def extract(
        self, content: bytes, media_type: str, max_pages: int
    ) -> EquipmentDocumentText:
        """Возвращает текст ограниченного числа страниц."""

    def render_page(self, content: bytes, page_number: int, max_pages: int) -> bytes:
        """Растрирует одну выбранную страницу в ограниченном бюджете."""


@dataclass(frozen=True, slots=True)
class ExtractEquipmentDocument:
    """Проверяет бюджет документа перед инфраструктурным извлечением."""

    reader: EquipmentDocumentReader
    max_bytes: int = 12_000_000
    max_pages: int = 40

    def execute(self, content: bytes, media_type: str) -> EquipmentDocumentText:
        """Отклоняет пустой или слишком большой документ."""
        if not content or len(content) > self.max_bytes:
            raise ValueError("Размер документа производителя недопустим.")
        if media_type not in {"application/pdf", "text/html"}:
            raise ValueError("Формат документа производителя не поддерживается.")
        return self.reader.extract(content, media_type, self.max_pages)
