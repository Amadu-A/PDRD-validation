# services/equipment-search-service/tests/integration/test_source_switch_database.py

"""Реальный PostgreSQL: решения сайта, сохранение HTTP, конфликты и аудит."""

import asyncio
import os
from uuid import uuid4

import pytest
from pdrd_equipment_search_service.application.resolve import ResolveEquipment
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.equipment import EquipmentIdentity, SearchHit
from pdrd_equipment_search_service.domain.source_management import (
    SourceAlreadyExistsError,
)
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)
from pdrd_equipment_search_service.infrastructure.postgres_sources import (
    PostgresSourceManager,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def catalog(tmp_path):
    """Разрешает только изолированную тестовую БД; удаляет только свои домены."""
    if os.getenv("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Нужен изолированный PostgreSQL Equipment.")
    url = make_url(Settings(_env_file=None).database.url())
    assert (url.host, url.database, url.username) == (
        "equipment-test-postgres",
        "pdrd_equipment_test",
        "equipment_test",
    )
    engine = create_async_engine(url)
    host = uuid4().hex + ".example.com"
    try:
        yield (
            PostgresSourceManager(engine),
            PostgresEquipmentCatalog(engine, tmp_path),
            host,
        )
    finally:
        async with engine.begin() as connection:
            params = {"hosts": [host, "www." + host]}
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.source_audit WHERE source_domain_id IN "
                    "(SELECT id FROM equipment_search.source_domains WHERE hostname = ANY(:hosts))"
                ),
                params,
            )
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.source_domains WHERE hostname = ANY(:hosts)"
                ),
                params,
            )
        await engine.dispose()


async def save(manager, host, **kwargs):
    """Меняет один официальный производитель, не затрагивая исходный реестр."""
    return await manager.update_source(
        manufacturer="Болид",
        hostname=host,
        status="trusted",
        enabled=True,
        reason="Интеграционная проверка",
        actor="test-actor",
        **kwargs,
    )


async def test_switch_keeps_legacy_http_and_audits_each_confirmed_decision(catalog):
    """Выключение явно блокирует сайт; включение восстанавливает разрешение и HTTP."""
    manager, lookup, host = catalog
    created = await save(manager, host, allow_http=True, create_only=True)
    for status, enabled in [("blocked", False), ("trusted", True)]:
        saved = await manager.update_source(
            manufacturer="Болид",
            hostname=host,
            status=status,
            enabled=enabled,
            reason="Решение переключателя",
            actor="test-actor",
        )
        assert saved["allow_http"] is True
        actual = await lookup.source_for("Болид", host)
        assert (
            actual.status == status and actual.enabled == enabled and actual.allow_http
        )
    history = await manager.audit(created["id"])
    assert len(history) == 3
    assert [(item["previous_status"], item["new_status"]) for item in history] == [
        (None, "trusted"),
        ("trusted", "blocked"),
        ("blocked", "trusted"),
    ]
    assert all(item["new_allow_http"] for item in history)
    assert history[1]["previous_allow_http"] is True


async def test_duplicate_does_not_modify_existing_source_and_www_is_distinct(catalog):
    """Добавление не превращается в редактирование; точные hostname независимы."""
    manager, lookup, host = catalog
    created = await save(manager, host, create_only=True)
    assert created["allow_http"] is False
    with pytest.raises(SourceAlreadyExistsError):
        await save(manager, host, create_only=True, allow_http=True)
    assert len(await manager.audit(created["id"])) == 1
    assert not (await lookup.source_for("Болид", host)).allow_http
    www = await save(manager, "www." + host, create_only=True)
    assert www["id"] != created["id"]


async def test_simultaneous_additions_return_one_conflict_without_second_audit(catalog):
    """Гонка двух форм не создаёт дубликат и не меняет первое решение."""
    manager, _, host = catalog
    results = await asyncio.gather(
        save(manager, host, create_only=True),
        save(manager, host, create_only=True),
        return_exceptions=True,
    )
    assert sum(isinstance(item, SourceAlreadyExistsError) for item in results) == 1
    saved = next(item for item in results if isinstance(item, dict))
    assert len(await manager.audit(saved["id"])) == 1


@pytest.mark.parametrize("allow_unverified", [False, True])
async def test_real_blocked_source_is_never_downloaded_even_with_override(
    catalog, allow_unverified
):
    """Настоящее решение из PostgreSQL проверяется сценарием поиска до загрузки."""
    manager, lookup, host = catalog
    await manager.update_source(
        manufacturer="Болид",
        hostname=host,
        status="blocked",
        enabled=False,
        reason="Запрет использования",
        actor="test-actor",
        allow_http=True,
    )

    class Search:
        """Детерминированная выдача только запрещённого домена."""

        async def search(self, query, limit):
            """Исключает зависимость проверки политики от внешнего поисковика."""
            return (SearchHit(f"https://{host}/c2000.pdf", "Болид С2000 паспорт"),)

    class Downloader:
        """Не допускает реальную сеть и фиксирует нарушение политики тестом."""

        async def download(self, *args, **kwargs):
            """Запрещённый сайт не должен доходить до этого адаптера."""
            raise AssertionError("Запрещённый сайт отправлен загрузчику")

    result = await ResolveEquipment(lookup, Search(), Downloader(), None).execute(
        EquipmentIdentity("Болид", "С2000"), allow_unverified=allow_unverified
    )
    assert result.document is None
    assert any(item.get("code") == "source_blocked" for item in result.attempts)
