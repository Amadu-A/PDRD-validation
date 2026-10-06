# services/user-service/tests/functional/test_section_distribution.py

"""Проверяет private RPC выдачи раздела, доверенные заголовки и безопасные отказы."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pdrd_user_service.application.use_cases.distribute_section import (
    CatalogSectionNotFound,
    CatalogWriteRequired,
    SectionDistribution,
)
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.main import create_app

ACTOR = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
SECTION = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
KEY = "test-section-distribution-internal-key-long-enough"
PATH = "/internal/v1/users/section-catalog/grants"
HEADERS = {"Authorization": f"Bearer {KEY}", "X-PDRD-Actor-Id": str(ACTOR)}


class Distribution:
    """Подменяет прикладной сценарий, фиксируя только проверенные UUID."""

    def __init__(self, error: Exception | None = None) -> None:
        """Хранит ожидаемый отказ и след вызовов."""
        self.error = error
        self.calls: list[tuple[UUID, UUID]] = []

    async def execute(
        self, *, section_id: UUID, actor_user_id: UUID
    ) -> SectionDistribution:
        """Возвращает проверенный результат или заданную прикладную ошибку."""
        self.calls.append((section_id, actor_user_id))
        if self.error is not None:
            raise self.error
        return SectionDistribution(section_id, 2, False)


def application(distribution: Distribution):
    """Собирает закрытый API без PostgreSQL и внешнего HTTP."""

    async def close() -> None:
        """Освобождать реальные соединения в HTTP-тесте не требуется."""

    return create_app(
        ApplicationContainer(
            settings=Settings(
                _env_file=None,
                enabled=True,
                internal_key=KEY,
                database=DatabaseSettings(password="private-test-password"),
            ),
            readiness=None,
            directory=None,
            shutdown_callback=close,
            section_distribution=distribution,
        )
    )


def test_private_distribution_passes_verified_ids_and_disables_cache() -> None:
    """Маршрут передаёт актёра только из доверенного header и не кеширует ответ."""
    distribution = Distribution()
    with TestClient(application(distribution)) as client:
        response = client.post(PATH, headers=HEADERS, json={"section_id": str(SECTION)})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "section_id": str(SECTION),
        "granted_users": 2,
        "already_distributed": False,
    }
    assert distribution.calls == [(SECTION, ACTOR)]


@pytest.mark.parametrize(
    "headers,payload,expected",
    [
        ({}, {"section_id": str(SECTION)}, 401),
        ({"Authorization": f"Bearer {KEY}"}, {"section_id": str(SECTION)}, 422),
        (HEADERS, {"section_id": "invalid"}, 422),
        (HEADERS, {"section_id": str(SECTION), "role": "platform_admin"}, 422),
        (HEADERS, {"section_id": str(SECTION), "actor_user_id": str(ACTOR)}, 422),
    ],
)
def test_private_distribution_rejects_untrusted_fields(
    headers: dict[str, str], payload: dict[str, str], expected: int
) -> None:
    """Браузер не задаёт роль, актёра или некорректный UUID в теле RPC."""
    distribution = Distribution()
    with TestClient(application(distribution)) as client:
        response = client.post(PATH, headers=headers, json=payload)
    assert response.status_code == expected
    assert distribution.calls == []


@pytest.mark.parametrize(
    "error,expected",
    [
        (CatalogWriteRequired("Секретная роль"), 403),
        (CatalogSectionNotFound("Приватный UUID"), 404),
        (SectionCatalogUnavailable("Приватный адрес"), 503),
    ],
)
def test_private_distribution_maps_expected_failures(
    error: Exception, expected: int
) -> None:
    """Отказы не выдают сетевые детали и не превращаются в успешное назначение."""
    with TestClient(application(Distribution(error))) as client:
        response = client.post(PATH, headers=HEADERS, json={"section_id": str(SECTION)})
    assert response.status_code == expected
    assert "Приватный" not in response.text
    assert "Секретная" not in response.text
