# services/equipment-search-service/tests/integration/test_source_priority_database.py

"""Реальный каталог читает приоритет из исходного реестра без миграции и изменения доверия."""

import os
from uuid import uuid4

import pytest
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)
from pdrd_equipment_search_service.infrastructure.postgres_sources import (
    PostgresSourceManager,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def test_seeded_official_and_manually_confirmed_catalog_priority(tmp_path):
    """Изначальный официальный домен отличается от вручную подтверждённого каталога."""
    if os.getenv("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Нужен изолированный PostgreSQL Equipment.")
    url = make_url(Settings(_env_file=None).database.url())
    assert (url.host, url.database, url.username) == (
        "equipment-test-postgres",
        "pdrd_equipment_test",
        "equipment_test",
    )
    engine = create_async_engine(url)
    catalog = PostgresEquipmentCatalog(engine, tmp_path)
    manager = PostgresSourceManager(engine)
    host = uuid4().hex + ".example.com"
    try:
        official = await catalog.source_for("Болид", "bolid.ru")
        assert official is not None and official.registry_official
        await manager.update_source(
            manufacturer="Болид",
            hostname=host,
            status="trusted",
            enabled=True,
            allow_http=False,
            reason="Проверенный тестовый каталог",
            actor="integration-test",
        )
        manual = await catalog.source_for("Болид", host)
        assert manual is not None and not manual.registry_official
        assert manual.matches("Болид", host)
        assert manual.status == official.status == "trusted"
        hosts = await catalog.trusted_hosts("Болид")
        assert hosts.index("bolid.ru") < hosts.index(host)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.source_audit WHERE source_domain_id IN "
                    "(SELECT id FROM equipment_search.source_domains WHERE hostname = :host)"
                ),
                {"host": host},
            )
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.source_domains WHERE hostname = :host"
                ),
                {"host": host},
            )
        await engine.dispose()
