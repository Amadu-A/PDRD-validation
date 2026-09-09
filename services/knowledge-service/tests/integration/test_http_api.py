# services/knowledge-service/tests/integration/test_http_api.py

"""HTTP integration tests Knowledge Service."""

from fastapi.testclient import TestClient
from pdrd_knowledge_service.application.use_cases.experience import (
    SearchExperience,
)
from pdrd_knowledge_service.application.use_cases.health import (
    CheckReadiness,
)
from pdrd_knowledge_service.application.use_cases.normative import (
    SearchNormative,
)
from pdrd_knowledge_service.application.use_cases.user_packages import (
    SearchUserPackages,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.core.settings import (
    Settings,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
)
from pdrd_knowledge_service.main import (
    create_app,
)


class FakeDatabaseReadinessProbe:
    """Fake PostgreSQL readiness probe HTTP tests."""

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает готовую database schema."""
        return True


class FakeEmbeddingProvider:
    """Fake embedding provider HTTP tests."""

    async def embed(
        self,
        texts: tuple[
            str,
            ...,
        ],
        *,
        instruction: str,
    ) -> list[list[float]]:
        """Возвращает один vector на каждый запрос."""
        assert instruction

        return [
            [
                float(
                    index,
                ),
            ]
            for index in range(
                1,
                len(
                    texts,
                )
                + 1,
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает готовую embedding model."""
        return True


class FakeVectorStore:
    """Fake vector store HTTP tests."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает point нужного типа."""
        assert vector
        assert limit > 0

        if collection == "normative-test":
            return [
                VectorPoint(
                    point_id="norm-1",
                    score=0.91,
                    payload={
                        "source_file": "СП-test.pdf",
                        "source_path": "/norms/СП-test.pdf",
                        "page": 12,
                        "chunk_index": 3,
                        "text": ("Тестовое нормативное требование."),
                    },
                )
            ]

        return [
            VectorPoint(
                point_id="experience-1",
                score=0.87,
                payload={
                    "project_id": "project-1",
                    "issue_id": "issue-1",
                    "issue_text": "Тестовое замечание",
                    "verified_fixed": True,
                    "before_context": "До исправления",
                    "after_context": "После исправления",
                },
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает готовый Qdrant."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает существование test collections."""
        return collection in {
            "normative-test",
            "experience-test",
        }


def build_client() -> TestClient:
    """Создаёт TestClient без реальных infrastructure dependencies."""
    settings = Settings(
        _env_file=None,
        service_name="PDRD Knowledge Service Test",
        service_version="0.1.0-test",
        environment="test",
    )

    database_probe = FakeDatabaseReadinessProbe()

    embedding_provider = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    search_normative = SearchNormative(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection="normative-test",
        embedding_model="embedding-test",
        top_k=4,
        max_sources=12,
    )

    search_user_packages = SearchUserPackages(
        managed_search=search_normative,
    )

    container = ApplicationContainer(
        settings=settings,
        search_normative=search_normative,
        search_user_packages=search_user_packages,
        search_experience=SearchExperience(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            collection="experience-test",
            embedding_model="embedding-test",
            top_k=3,
        ),
        check_readiness=CheckReadiness(
            database_probe=database_probe,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            normative_collection="normative-test",
            experience_collection="experience-test",
        ),
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_health_endpoints() -> None:
    """Проверяет liveness и readiness."""
    with build_client() as client:
        live_response = client.get(
            "/health/live",
        )

        ready_response = client.get(
            "/health/ready",
        )

    assert live_response.status_code == 200

    assert live_response.json() == {
        "status": "ok",
        "service": "PDRD Knowledge Service Test",
        "version": "0.1.0-test",
    }

    assert ready_response.status_code == 200

    assert ready_response.json() == {
        "status": "ready",
        "service": "PDRD Knowledge Service Test",
        "version": "0.1.0-test",
        "dependencies": {
            "database": True,
            "embedding_model": True,
            "qdrant": True,
            "normative_collection": True,
            "experience_collection": True,
        },
    }


def test_normative_search_endpoint() -> None:
    """Проверяет нормативный HTTP contract."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/search/normative",
            json={
                "queries": [
                    "заземление оборудования",
                ]
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["queries"] == [
        "заземление оборудования",
    ]

    assert payload["embedding_model"] == "embedding-test"

    source = payload["sources"][0]

    assert source["source_id"] == "N1"

    assert source["source_file"] == "СП-test.pdf"

    assert source["page"] == 12


def test_user_package_search_without_scope_returns_empty_sources() -> None:
    """Проверяет HTTP contract package retrieval без selected packages."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/search/user-packages",
            json={
                "queries": [
                    "требования заказчика",
                ]
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["queries"] == [
        "требования заказчика",
    ]

    assert payload["sources"] == []

    assert payload["embedding_model"] == "embedding-test"


def test_experience_search_endpoint() -> None:
    """Проверяет HTTP contract Базы Опыта."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/search/experience",
            json={
                "queries": [
                    "нет защитного заземления",
                ]
            },
        )

    assert response.status_code == 200

    payload = response.json()

    result = payload["results"][0]

    assert result["query"] == ("нет защитного заземления")

    source = result["sources"][0]

    assert source["source_id"] == "E1"

    assert source["verified_fixed"] is True
