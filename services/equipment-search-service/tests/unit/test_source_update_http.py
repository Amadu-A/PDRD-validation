# services/equipment-search-service/tests/unit/test_source_update_http.py

"""Внутренний HTTP-контракт переключателя: транспорт, конфликт и инициатор."""

import httpx
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.source_management import (
    SourceAlreadyExistsError,
)
from pdrd_equipment_search_service.main import create_app


class Sources:
    """Записывает параметры без подмены проверки HTTP-авторизации."""

    def __init__(self):
        """Готовит журнал вызовов."""
        self.calls = []

    async def update_source(self, **kwargs):
        """Возвращает контролируемый конфликт повторного добавления."""
        self.calls.append(kwargs)
        if kwargs["create_only"]:
            raise SourceAlreadyExistsError("Сайт уже добавлен")
        return {"status": kwargs["status"], "enabled": kwargs["enabled"]}


async def test_source_update_preserves_omitted_http_and_maps_duplicate_to_conflict():
    """Отсутствие HTTP передаётся как None; конфликт не становится 422/503."""
    manager = Sources()
    settings = Settings(_env_file=None, internal_key="test-key")
    app = create_app(settings=settings, source_manager=manager)
    app.state.sources = manager
    body = {
        "manufacturer": "Болид",
        "hostname": "bolid.ru",
        "status": "blocked",
        "enabled": False,
        "reason": "Решение",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put("/internal/v1/equipment-search/sources", json=body)
        assert response.status_code == 403 and not manager.calls
        headers = {"X-Internal-Key": "test-key", "X-Actor": "verified-user"}
        response = await client.put(
            "/internal/v1/equipment-search/sources", json=body, headers=headers
        )
        assert response.status_code == 200
        assert (
            manager.calls[0]["allow_http"] is None
            and manager.calls[0]["actor"] == "verified-user"
        )
        response = await client.put(
            "/internal/v1/equipment-search/sources",
            json={**body, "create_only": True},
            headers=headers,
        )
        assert response.status_code == 409
