# services/experience-service/tests/unit/test_catalog_authors_http.py

"""Приватный HTTP-контракт авторов и точный фильтр передаются в прикладной каталог."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_experience_service.transport.http.routers.catalog import router
from pydantic import SecretStr


class Catalog:
    """Наблюдаемый прикладной каталог без SQL в транспортном тесте."""

    def __init__(self):
        """Фиксирует фильтры и границы страницы справочника."""
        self.criteria, self.page = None, None

    async def list(self, criteria):
        """Возвращает пустую страницу, сохраняя точную строку автора."""
        self.criteria = criteria
        return (), 0

    async def authors(self, **page):
        """Исторические идентичности не теряются до обогащения в Gateway."""
        self.page = page
        return ("engineer:legacy",), 1


def test_private_authors_route_precedes_uuid_and_preserves_exact_filter():
    """Без служебного ключа данные закрыты; '%' автора не превращается в wildcard."""
    catalog = Catalog()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(internal_key=SecretStr("private-key")),
        catalog=catalog,
        export_catalog=object(),
    )
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/internal/v1/experience/authors").status_code == 403
        headers = {
            "Authorization": "Bearer private-key",
            "X-Review-Actor": "user:server",
        }
        authors = client.get(
            "/internal/v1/experience/authors",
            params={"offset": 0, "limit": 10},
            headers=headers,
        )
        assert authors.status_code == 200
        assert authors.json()["items"] == [{"id": "engineer:legacy"}]
        assert catalog.page == {"offset": 0, "limit": 10}
        selected = client.get(
            "/internal/v1/experience",
            params={"author": "user:literal%"},
            headers=headers,
        )
        assert (
            selected.status_code == 200 and catalog.criteria.author == "user:literal%"
        )
        assert (
            client.get(
                "/internal/v1/experience/authors",
                params={"limit": 101},
                headers=headers,
            ).status_code
            == 422
        )
