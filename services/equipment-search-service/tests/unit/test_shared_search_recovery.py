# services/equipment-search-service/tests/unit/test_shared_search_recovery.py

"""Регрессии актуального доверия и извлечения фактов после текстового прохода."""

from dataclasses import replace

import pytest
from pdrd_equipment_search_service.application.resolve import ResolveEquipment
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.domain.equipment import (
    SourceDomain,
    SourceStatus,
)

from .test_resolve_equipment import (
    IDENTITY,
    Catalog,
    Downloader,
    Inspector,
    Search,
    SearchHit,
    snapshot,
)


@pytest.mark.parametrize(
    ("stored", "current", "expected"),
    [
        ("unverified_user_override", SourceStatus.TRUSTED, "trusted"),
        ("trusted", SourceStatus.PENDING, "unverified_user_override"),
    ],
)
async def test_cached_document_uses_current_source_decision(stored, current, expected):
    """Доверие нового задания вычисляется заново, исторический snapshot не меняется."""
    original = replace(snapshot(), trust_status=stored)
    catalog = Catalog(
        local=original,
        sources={"meanwell.com": SourceDomain("MEAN WELL", "meanwell.com", current)},
    )
    result = await ResolveEquipment(
        catalog, Search(), Downloader(), Inspector()
    ).execute(IDENTITY, allow_unverified=True)
    assert result.document.trust_status == expected
    assert original.trust_status == stored


class EmptyFacts:
    """Первый текстовый проход пуст, адресная транскрипция даёт реальный факт."""

    def __init__(self):
        """Запоминает переданные доказательства."""
        self.calls = []

    async def extract_or_get(self, identity, document, pages, *, vision_facts=()):
        """Не генерирует характеристику до адресного чтения документа."""
        self.calls.append(vision_facts)
        return "ready", tuple(vision_facts)


class Vision:
    """Подменяет только внешнее визуальное чтение, учитывая общий бюджет."""

    async def inspect(
        self, identity, document, inspection, *, budget, should_stop=None
    ):
        """Возвращает характеристику выбранной страницы, если разрешена операция."""
        assert inspection.applicable
        assert budget.claim()
        return replace(
            inspection,
            vision_facts=(
                {"page": 2, "property_name": "Output voltage", "value_raw": "24"},
            ),
            requires_vision=False,
        )


async def test_readable_pdf_without_text_facts_reaches_targeted_vision():
    """Читаемый PDF с таблицей не объявляется проверенным при пустом extraction."""
    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    index = EmptyFacts()
    budget = EquipmentVisionBudget(1)
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://meanwell.com/manual.pdf"),)),
        Downloader(),
        Inspector(),
        fact_index=index,
        vision_inspector=Vision(),
    ).execute(IDENTITY, vision_budget=budget)
    assert result.facts and result.status == "found"
    assert len(index.calls) == 2 and budget.remaining == 0


async def test_empty_facts_are_incomplete_instead_of_success():
    """Доступный файл сам по себе не доказывает извлечение характеристик."""
    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://meanwell.com/manual.pdf"),)),
        Downloader(),
        Inspector(),
        fact_index=EmptyFacts(),
    ).execute(IDENTITY)
    assert result.document is not None and not result.facts
    assert result.status == "incomplete" and result.reason_code == "facts_unavailable"
