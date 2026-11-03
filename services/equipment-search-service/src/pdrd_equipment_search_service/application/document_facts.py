# services/equipment-search-service/src/pdrd_equipment_search_service/application/document_facts.py

"""Текстовое извлечение, затем ограниченное адресное чтение пустого PDF-паспорта."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pdrd_equipment_search_service.application.ports import (
    DocumentInspector,
    EquipmentCatalog,
    EquipmentFactIndex,
    EquipmentVisionInspector,
)
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.core.observability import log_execution_time
from pdrd_equipment_search_service.domain.diagnostics import ReasonCode
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
)


@log_execution_time(operation="equipment.document_facts")
async def extract_document_facts(
    *,
    catalog: EquipmentCatalog,
    index: EquipmentFactIndex,
    vision: EquipmentVisionInspector | None,
    identity: EquipmentIdentity,
    snapshot: DocumentSnapshot,
    pages: tuple[tuple[int, str], ...],
    visual: tuple[dict[str, Any], ...],
    budget: EquipmentVisionBudget,
    inspector: DocumentInspector,
    should_stop: Callable[[], Awaitable[bool]] | None = None,
    document: DownloadedDocument | None = None,
    inspection: DocumentInspection | None = None,
) -> tuple[str, tuple[dict[str, Any], ...], str]:
    """Не выдаёт доступность файла за проверку, не повторяет уже сохранённое VLM-чтение."""

    async def checkpoint() -> None:
        """Проверяет отмену до очередного внешнего действия."""
        if should_stop is not None and await should_stop():
            raise asyncio.CancelledError

    await checkpoint()
    status, facts = await index.extract_or_get(
        identity, snapshot, pages, vision_facts=visual
    )
    if facts:
        return status, facts, ""
    if visual:
        return status, (), ReasonCode.FACTS_UNAVAILABLE
    await checkpoint()
    if document is None:
        saved, content, media_type = await catalog.load_document(snapshot.source_id)
        document = DownloadedDocument(content, saved.final_url, media_type)
        if saved.sha256 != snapshot.sha256 or document.sha256 != snapshot.sha256:
            raise ValueError("SHA сохранённого документа изменён перед EQ извлечением.")
    if inspection is None:
        await checkpoint()
        inspection = await inspector.inspect(identity, document)
        if not inspection.applicable and not inspection.requires_vision:
            return status, (), inspection.reason_code or ReasonCode.FACTS_UNAVAILABLE
        pages = inspection.pages
        await checkpoint()
        status, facts = await index.extract_or_get(identity, snapshot, pages)
        if facts:
            return status, facts, ""
    if document.media_type != "application/pdf":
        return status, (), ReasonCode.FACTS_UNAVAILABLE
    if snapshot.applicability == "technical_reference":
        return status, (), ReasonCode.FACTS_UNAVAILABLE
    if vision is None:
        return status, (), ReasonCode.FACTS_UNAVAILABLE
    if budget.remaining <= 0:
        return status, (), ReasonCode.VISION_BUDGET
    # Приоритет у технических страниц, а не обложки с единственной маркировкой.
    technical = (
        r"характерист|параметр|specification|technical|voltage|напряжен|current|\bток\b"
    )
    candidates = tuple(
        number for number, text in pages if re.search(technical, text, re.I)
    )
    original = inspection or DocumentInspection(
        True, pages=pages, manufacturer=identity.manufacturer
    )
    aliases = await catalog.manufacturer_aliases(identity.manufacturer)
    target = replace(
        original,
        manufacturer_aliases=aliases,
        vision_pages=tuple(
            dict.fromkeys(
                (
                    *(page for page in original.vision_pages if page in candidates),
                    *original.vision_pages,
                    *candidates,
                )
            )
        ),
    )
    await checkpoint()
    verified = await vision.inspect(
        identity, document, target, budget=budget, should_stop=should_stop
    )
    if not verified.vision_facts:
        return status, (), verified.reason_code or ReasonCode.FACTS_UNAVAILABLE
    await checkpoint()
    status, facts = await index.extract_or_get(
        identity, snapshot, pages, vision_facts=verified.vision_facts
    )
    return status, facts, "" if facts else ReasonCode.FACTS_UNAVAILABLE
