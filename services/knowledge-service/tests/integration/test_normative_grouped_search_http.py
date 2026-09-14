# services/knowledge-service/tests/integration/test_normative_grouped_search_http.py

"""HTTP integration tests batched grouped normative retrieval."""

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
    """Fake database probe."""

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


class RecordingEmbeddingProvider:
    """Fake embedding provider HTTP integration test."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журнал batch calls."""
        self.calls: list[
            tuple[
                str,
                ...,
            ]
        ] = []

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float,]]:
        """Возвращает deterministic vectors."""
        assert instruction

        self.calls.append(
            texts,
        )

        return [
            [
                float(
                    index,
                )
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
        """Возвращает readiness."""
        return True


class FakeVectorStore:
    """Fake Qdrant HTTP integration test."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает отдельный нормативный point."""
        assert limit > 0

        index = int(
            vector[0],
        )

        if collection == "normative-test":
            return [
                VectorPoint(
                    point_id=f"norm-{index}",
                    score=0.91,
                    payload={
                        "source_file": f"СП-{index}.pdf",
                        "page": index,
                        "chunk_index": index,
                        "text": (f"Нормативное требование {index}."),
                    },
                )
            ]

        return [
            VectorPoint(
                point_id=f"experience-{index}",
                score=0.87,
                payload={
                    "project_id": "project",
                    "issue_id": f"issue-{index}",
                    "issue_text": "Замечание",
                    "verified_fixed": True,
                    "before_context": "До",
                    "after_context": "После",
                },
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает существование test collection."""
        return collection in {
            "normative-test",
            "experience-test",
        }


def _build_client() -> tuple[
    TestClient,
    RecordingEmbeddingProvider,
]:
    """Создаёт Knowledge Service test application."""
    settings = Settings(
        _env_file=None,
        service_name="PDRD Knowledge Service Test",
        service_version="0.1.0-test",
        environment="test",
    )

    embedding = RecordingEmbeddingProvider()

    vector_store = FakeVectorStore()

    search_normative = SearchNormative(
        embedding_provider=embedding,
        vector_store=vector_store,
        collection="normative-test",
        embedding_model="embedding-test",
        top_k=4,
        max_sources=12,
    )

    container = ApplicationContainer(
        settings=settings,
        search_normative=search_normative,
        search_user_packages=SearchUserPackages(
            managed_search=search_normative,
        ),
        search_experience=SearchExperience(
            embedding_provider=embedding,
            vector_store=vector_store,
            collection="experience-test",
            embedding_model="embedding-test",
            top_k=3,
        ),
        check_readiness=CheckReadiness(
            database_probe=FakeDatabaseReadinessProbe(),
            embedding_provider=embedding,
            vector_store=vector_store,
            normative_collection="normative-test",
            experience_collection="experience-test",
        ),
    )

    return (
        TestClient(
            create_app(
                container=container,
            )
        ),
        embedding,
    )


def test_grouped_http_request_uses_one_embedding_batch() -> None:
    """Один HTTP grouped request создаёт один embedding batch."""
    client, embedding = _build_client()

    with client:
        response = client.post(
            "/internal/v1/search/normative-grouped",
            json={
                "queries": [
                    "маркировка кабелей",
                    "защитное заземление",
                    "маркировка кабелей",
                ]
            },
        )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert embedding.calls == [
        (
            "маркировка кабелей",
            "защитное заземление",
        ),
    ]

    assert (
        len(
            payload["results"],
        )
        == 3
    )

    assert [result["query"] for result in payload["results"]] == [
        "маркировка кабелей",
        "защитное заземление",
        "маркировка кабелей",
    ]

    assert payload["results"][0]["sources"][0]["point_id"] == "norm-1"

    assert payload["results"][1]["sources"][0]["point_id"] == "norm-2"

    assert payload["results"][2]["sources"][0]["point_id"] == "norm-1"

    assert payload["results"][0]["sources"] == payload["results"][2]["sources"]

    assert payload["results"][0]["sources"] != payload["results"][1]["sources"]
