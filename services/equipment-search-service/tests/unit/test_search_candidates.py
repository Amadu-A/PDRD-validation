# services/equipment-search-service/tests/unit/test_search_candidates.py

"""Регрессия приоритета сайтов и отсева чужих ссылок без расхода загрузки."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pdrd_equipment_search_service.application.resolve import ResolveEquipment
from pdrd_equipment_search_service.application.search_budget import (
    EquipmentSearchBudget,
)
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SearchHit,
    SourceDomain,
    SourceStatus,
)
from pdrd_equipment_search_service.domain.search_candidates import clearly_other_model


@pytest.mark.parametrize(
    ("model", "title", "path", "expected"),
    [
        ("Сигнал-10", "Сигнал-100 руководство", "/manual.pdf", True),
        ("Сигнал-10", "Сигнал-10 и Сигнал-100", "/manual.pdf", False),
        ("Сигнал-10", "Сигнал 10 руководство", "/manual.pdf", False),
        ("Сигнал-10", "Каталог Сигнал-100", "/manual.pdf", False),
        ("Сигнал-10", "Сигнал-20 и Сигнал-100", "/manual.pdf", False),
        ("Сигнал-10", "Серия Сигнал-100", "/manual.pdf", False),
        ("РИП-12", "", "/рип-12-51.pdf", False),
        ("РИП-12", "", "/рип-24.pdf", True),
        ("ИО 102-29", "ИО 102-28", "/manual.pdf", True),
        ("ИО 102-29", "ИО 102–29 Эстет-Сейф", "/manual.pdf", False),
        ("NXB-63", "", "/NXB-125.pdf", True),
        ("NXB-63", "", "/NXB_63.pdf", False),
        ("NXB-63", "", "/manual.pdf?model=NXB-125&token=secret", False),
        ("NXB-63", "Download PDF", "/manual-2024.pdf", False),
        ("NXB-63", "Модель: NXB-125", "/manual.pdf", True),
        ("NXB-63", "Каталог. Модель: NXB-125", "/manual.pdf", False),
        ("NXB-63", "FRENIC-Mini руководство", "/manual.pdf", False),
        ("Сигнал-10", "Модель: FRENIC-Mini", "/manual.pdf", True),
        (
            "Сигнал-10",
            "Общее руководство",
            "/%D1%81%D0%B8%D0%B3%D0%BD%D0%B0%D0%BB-100.pdf",
            True,
        ),
    ],
)
def test_metadata_rejects_only_clear_other_model(model, title, path, expected):
    """Исполнения, каталоги и неполные заголовки не отвергают пригодные документы."""
    assert (
        clearly_other_model(
            EquipmentIdentity("Производитель", model),
            SearchHit("https://example.com" + path, title),
        )
        is expected
    )


def resolver_with_sources(sources, hits, *, applicable=True):
    """Собирает сценарий с контрактными doubles, не выполняя реальную сеть."""

    async def source_for(manufacturer, host):
        """Доверие выдаётся только для точного производителя и домена."""
        return sources.get(host)

    async def download(url, **options):
        """Запоминаемый загрузчик возвращает минимальный документ."""
        return DownloadedDocument(b"%PDF-1.7", url, "application/pdf")

    async def save_document(identity, source_url, document, inspection, trust_status):
        """Сохранение возвращает контрактный snapshot, а не успешный результат без документа."""
        return DocumentSnapshot(
            "EQ-test",
            identity.manufacturer,
            identity.model,
            identity.variant,
            source_url,
            document.final_url,
            document.sha256,
            "test/manual.pdf",
            "",
            trust_status,
        )

    catalog = SimpleNamespace(
        find_document=AsyncMock(return_value=None),
        source_for=AsyncMock(side_effect=source_for),
        trusted_hosts=AsyncMock(return_value=()),
        register_pending=AsyncMock(),
        save_document=AsyncMock(side_effect=save_document),
    )
    search = SimpleNamespace(search=AsyncMock(return_value=tuple(hits)))
    downloader = SimpleNamespace(download=AsyncMock(side_effect=download))
    inspector = SimpleNamespace(
        inspect=AsyncMock(return_value=DocumentInspection(applicable))
    )
    return ResolveEquipment(catalog, search, downloader, inspector), downloader, catalog


async def test_official_registry_then_trusted_catalog_then_unverified():
    """Официальный источник получает первую загрузку при обратном поисковом порядке."""
    identity = EquipmentIdentity("Болид", "Сигнал-10")
    sources = {
        "bolid.ru": SourceDomain(
            "Болид", "bolid.ru", SourceStatus.TRUSTED, registry_official=True
        ),
        "catalog.example": SourceDomain(
            "Болид", "catalog.example", SourceStatus.TRUSTED
        ),
    }
    hits = [
        SearchHit("https://unknown.example/manual.pdf"),
        SearchHit("https://catalog.example/manual.pdf"),
        SearchHit("https://bolid.ru/manual.pdf"),
    ]
    resolver, downloader, _ = resolver_with_sources(sources, hits, applicable=False)
    budget = EquipmentSearchBudget(1, 3)
    await resolver.execute(
        identity, allow_unverified=True, search_budget=budget, round_index=0
    )
    assert [call.args[0] for call in downloader.download.await_args_list] == [
        hits[2].url,
        hits[1].url,
        hits[0].url,
    ]


async def test_wrong_link_skipped_without_download_or_inspection():
    """Неподходящая ссылка не забирает единственную первоначальную попытку модели."""
    sources = {
        "bolid.ru": SourceDomain(
            "Болид", "bolid.ru", SourceStatus.TRUSTED, registry_official=True
        )
    }
    wrong = SearchHit("https://bolid.ru/signal-100.pdf", "Сигнал-100 руководство")
    right = SearchHit("https://bolid.ru/signal-10.pdf", "Сигнал-10 руководство")
    resolver, downloader, _ = resolver_with_sources(sources, [wrong, right])
    budget = EquipmentSearchBudget(1, 1)
    result = await resolver.execute(
        EquipmentIdentity("Болид", "Сигнал-10"), search_budget=budget, round_index=0
    )
    downloader.download.assert_awaited_once()
    assert downloader.download.await_args.args[0] == right.url
    assert result.attempts[0] == {
        "stage": "candidate",
        "code": "link_model_mismatch",
        "hostname": "bolid.ru",
    }
    assert budget.downloads_remaining == 0
    assert len(result.tried_url_hashes) == 2


@pytest.mark.parametrize(
    "status,enabled",
    [
        (SourceStatus.BLOCKED, True),
        (SourceStatus.TRUSTED, False),
        (SourceStatus.PENDING, True),
    ],
)
async def test_priority_never_grants_permission(status, enabled):
    """Происхождение из официального реестра не отменяет последующее решение о доверии."""
    source = SourceDomain(
        "Болид", "bolid.ru", status, enabled=enabled, registry_official=True
    )
    resolver, downloader, catalog = resolver_with_sources(
        {"bolid.ru": source}, [SearchHit("https://bolid.ru/manual.pdf")]
    )
    await resolver.execute(EquipmentIdentity("Болид", "Сигнал-10"), round_index=0)
    downloader.download.assert_not_awaited()
    catalog.save_document.assert_not_awaited()
