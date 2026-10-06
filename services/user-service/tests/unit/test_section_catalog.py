# services/user-service/tests/unit/test_section_catalog.py

"""Проверяет, что сбой каталога не превращается в успешный пустой доступ."""

from uuid import UUID

import httpx
import pytest
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pdrd_user_service.infrastructure.section_catalog import KnowledgeSectionCatalog


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [[], [{"section_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "name": "Раздел"}]],
)
async def test_catalog_reads_valid_snapshot(payload: list[dict[str, str]]) -> None:
    """Пустой каталог допустим только после проверенного успешного ответа."""

    def respond(request: httpx.Request) -> httpx.Response:
        """Проверяет фиксированный private маршрут и отсутствие персональных данных."""
        assert request.url.path == "/internal/v1/normative/sections"
        assert not request.url.query
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        base_url="http://knowledge-service:8401", transport=httpx.MockTransport(respond)
    ) as client:
        ids = await KnowledgeSectionCatalog(client).list_section_ids()
    assert ids == tuple(UUID(section["section_id"]) for section in payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload",
    [(503, []), (200, {}), (200, [{"section_id": "bad"}]), (200, [{}])],
)
async def test_catalog_rejects_failure_and_malformed_response(
    status: int, payload: object
) -> None:
    """Ошибка и неверный JSON запрещают частичное создание учётной записи."""
    async with httpx.AsyncClient(
        base_url="http://knowledge-service:8401",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload)
        ),
    ) as client:
        with pytest.raises(SectionCatalogUnavailable):
            await KnowledgeSectionCatalog(client).list_section_ids()


@pytest.mark.asyncio
async def test_catalog_network_error_is_unavailable() -> None:
    """Тайм-аут не разрешает молча создать пользователя без разделов."""

    def fail(request: httpx.Request) -> httpx.Response:
        """Имитирует тайм-аут фиксированного внутреннего запроса."""
        raise httpx.ConnectTimeout("Неверный приватный адрес", request=request)

    async with httpx.AsyncClient(
        base_url="http://knowledge-service:8401", transport=httpx.MockTransport(fail)
    ) as client:
        with pytest.raises(SectionCatalogUnavailable):
            await KnowledgeSectionCatalog(client).list_section_ids()


@pytest.mark.asyncio
async def test_catalog_excludes_sections_being_deleted() -> None:
    """Удаляемый раздел не назначается вновь подтверждённому пользователю."""
    async with httpx.AsyncClient(
        base_url="http://knowledge-service:8401",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[
                    {
                        "section_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                        "deleting": True,
                    }
                ],
            )
        ),
    ) as client:
        assert await KnowledgeSectionCatalog(client).list_section_ids() == ()
