# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/inspect_document.py

"""Проверка модели через текст, извлечённый существующим Document Service."""

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

import httpx

from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
)


def _contains_identifier(text: str, identifier: str) -> bool:
    """Ищет точную маркировку без совпадения с более длинной моделью."""
    if not identifier.strip():
        return True
    compact = re.sub(r"\s+", " ", text).casefold()
    needle = re.escape(re.sub(r"\s+", " ", identifier.strip()).casefold())
    return re.search(rf"(?<![\w-]){needle}(?![\w-])", compact) is not None


@dataclass(slots=True)
class DocumentServiceInspector:
    """Использует выделенный text-only контракт Document Service."""

    client: httpx.AsyncClient
    base_url: str
    timeout_seconds: float = 30.0

    async def inspect(
        self,
        identity: EquipmentIdentity,
        document: DownloadedDocument,
    ) -> DocumentInspection:
        """Отклоняет документ без производителя, модели или исполнения."""
        response = await self.client.post(
            f"{self.base_url.rstrip('/')}/internal/v1/equipment-documents/extract",
            files={"file": ("documentation", document.content, document.media_type)},
            data={"media_type": document.media_type},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        pages = tuple(
            (int(page["page"]), str(page["text"]))
            for page in payload.get("pages", [])
            if isinstance(page, dict)
        )
        text = " ".join(page_text for _, page_text in pages)
        if document.media_type == "application/pdf" and not text.strip():
            return DocumentInspection(
                False,
                reason="Сканированный PDF требует адресного визуального извлечения.",
                pages=pages,
                requires_vision=True,
            )
        if not _contains_identifier(text, identity.manufacturer):
            return DocumentInspection(
                False,
                reason="Производитель не найден в документе.",
                pages=pages,
            )
        if not _contains_identifier(text, identity.model):
            return DocumentInspection(
                False,
                reason="Модель не найдена в документе.",
                pages=pages,
            )
        if identity.variant and not _contains_identifier(text, identity.variant):
            return DocumentInspection(
                False,
                reason="Исполнение не подтверждено документом.",
                pages=pages,
            )
        revision = ""
        match = re.search(
            r"(?:rev(?:ision)?[.\s:]*|версия[.\s:]*)([\w.\-]{1,24})",
            text[:8000],
            re.IGNORECASE,
        )
        if match:
            revision = match.group(1)
        publication_date = ""
        date_match = re.search(
            r"(?:publication\s+date|issue\s+date|дата\s+(?:выпуска|издания))"
            r"\s*[:№-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})",
            text[:8000],
            re.IGNORECASE,
        )
        if date_match:
            raw_date = date_match.group(1)
            date_format = "%Y-%m-%d" if "-" in raw_date else "%d.%m.%Y"
            with suppress(ValueError):
                publication_date = (
                    datetime.strptime(raw_date, date_format).date().isoformat()
                )
        return DocumentInspection(
            True,
            revision=revision,
            publication_date=publication_date,
            pages=pages,
        )
