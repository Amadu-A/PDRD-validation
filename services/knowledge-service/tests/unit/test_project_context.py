# services/knowledge-service/tests/unit/test_project_context.py

"""Unit tests reusable Knowledge Project Context cache."""

from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.ports.vector_store import (
    StoredVectorPayload,
)
from pdrd_knowledge_service.application.use_cases.project_context import (
    CreateProjectContext,
    DeleteProjectContext,
    ResolveProjectContextCache,
    SearchProjectContext,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextTextPage,
    ProjectContextValidationItem,
    ProjectContextValidationSnapshot,
    VectorRecord,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
)


class FakeEmbeddingProvider:
    """Fake embedding provider."""

    def __init__(
        self,
    ) -> None:
        """Создаёт call history."""
        self.instructions: list[str | None] = []

        self.calls: list[
            tuple[
                str,
                ...,
            ]
        ] = []

    async def embed(
        self,
        texts: tuple[
            str,
            ...,
        ],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        self.instructions.append(
            instruction,
        )

        self.calls.append(
            texts,
        )

        return [
            [
                float(
                    index + 1,
                ),
                0.5,
                0.25,
            ]
            for index, _ in enumerate(
                texts,
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


class FakeVectorStore:
    """In-memory vector store с aliases."""

    def __init__(
        self,
    ) -> None:
        """Создаёт test state."""
        self.collections: dict[
            str,
            list[VectorRecord],
        ] = {}

        self.vector_sizes: dict[
            str,
            int,
        ] = {}

        self.aliases: dict[
            str,
            str,
        ] = {}

    def _resolve(
        self,
        collection: str,
    ) -> str:
        """Разрешает alias в physical collection."""
        return self.aliases.get(
            collection,
            collection,
        )

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает первый сохранённый chunk."""
        assert vector

        physical = self._resolve(
            collection,
        )

        records = self.collections[physical]

        return [
            VectorPoint(
                point_id=(records[0].point_id),
                score=0.87,
                payload=(records[0].payload),
            )
        ][:limit]

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт physical collection."""
        assert vector_size == 3

        self.collections[collection] = []

        self.vector_sizes[collection] = vector_size

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет records."""
        self.collections[collection].extend(
            records,
        )

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Удаляет collection и aliases на неё."""
        existed = collection in self.collections

        self.collections.pop(
            collection,
            None,
        )

        self.vector_sizes.pop(
            collection,
            None,
        )

        aliases_to_delete = [
            alias
            for (
                alias,
                target,
            ) in self.aliases.items()
            if target == collection or alias == collection
        ]

        for alias in aliases_to_delete:
            self.aliases.pop(
                alias,
                None,
            )

        return existed

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет collection либо alias."""
        return collection in self.collections or collection in self.aliases

    async def get_alias_target(
        self,
        alias: str,
    ) -> str | None:
        """Возвращает target test alias."""
        return self.aliases.get(
            alias,
        )

    async def replace_aliases(
        self,
        aliases_to_targets: dict[
            str,
            str,
        ],
    ) -> None:
        """Atomically заменяет aliases."""
        for (
            alias,
            target,
        ) in aliases_to_targets.items():
            assert target in self.collections

            self.aliases[alias] = target

    async def scroll_payloads(
        self,
        *,
        collection: str,
        batch_size: int = 256,
    ) -> tuple[
        StoredVectorPayload,
        ...,
    ]:
        """Возвращает persisted payloads."""
        assert batch_size > 0

        physical = self._resolve(
            collection,
        )

        return tuple(
            StoredVectorPayload(
                point_id=record.point_id,
                payload=dict(
                    record.payload,
                ),
            )
            for record in self.collections.get(
                physical,
                [],
            )
        )


def pages(
    *,
    suffix: str = "",
) -> tuple[
    ProjectContextTextPage,
    ...,
]:
    """Возвращает deterministic test ПЗ."""
    return (
        ProjectContextTextPage(
            page_number=5,
            text=(
                f"Пояснительная записка. Описание проектного решения. {suffix} " * 20
            ),
        ),
        ProjectContextTextPage(
            page_number=6,
            text=(
                "Технические решения проекта. "
                "Описание оборудования и кабелей. "
                f"{suffix} " * 20
            ),
        ),
    )


def validation() -> ProjectContextValidationSnapshot:
    """Возвращает accepted validation snapshot."""
    classifications = (
        ProjectContextValidationItem(
            page_number=5,
            kind="explanatory_note",
            confidence=0.99,
            reason="Пояснительная записка.",
        ),
        ProjectContextValidationItem(
            page_number=6,
            kind="explanatory_note",
            confidence=0.98,
            reason="Технические решения.",
        ),
    )

    return ProjectContextValidationSnapshot(
        enabled=True,
        pages_count=2,
        classifications=classifications,
        warnings=(),
    )


def resolver(
    vector_store: FakeVectorStore,
) -> ResolveProjectContextCache:
    """Создаёт cache resolver."""
    return ResolveProjectContextCache(
        vector_store=vector_store,  # type: ignore[arg-type]
        collection_prefix=("pdrd_project_context"),
        embedding_model=("test-embedding"),
        embedding_dimension=3,
        embedding_schema_version=1,
        chunk_size=120,
        chunk_overlap=20,
    )


def creator(
    embeddings: FakeEmbeddingProvider,
    vector_store: FakeVectorStore,
) -> CreateProjectContext:
    """Создаёт cache builder."""
    return CreateProjectContext(
        embedding_provider=embeddings,  # type: ignore[arg-type]
        vector_store=vector_store,  # type: ignore[arg-type]
        collection_prefix=("pdrd_project_context"),
        embedding_model=("test-embedding"),
        embedding_dimension=3,
        embedding_schema_version=1,
        chunk_size=120,
        chunk_overlap=20,
        embed_batch_size=12,
        upsert_batch_size=64,
    )


@pytest.mark.asyncio
async def test_project_context_cache_miss_build_hit() -> None:
    """Первый run строит cache, второй получает HIT без embeddings."""
    analysis_context_id = uuid4()

    embeddings = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    resolve = resolver(
        vector_store,
    )

    initial = await resolve.execute(
        context_id=analysis_context_id,
        enabled=True,
        pages=pages(),
    )

    assert initial.enabled is True

    assert initial.cache_hit is False

    assert initial.cache_key

    assert initial.context_id != analysis_context_id

    create = creator(
        embeddings,
        vector_store,
    )

    info = await create.execute(
        context_id=initial.context_id,
        enabled=True,
        pages=pages(),
        cache_key=initial.cache_key,
        validation=validation(),
    )

    assert info.enabled is True

    assert info.cache_hit is False

    assert info.context_id == initial.context_id

    assert info.cache_key == (initial.cache_key)

    assert info.collection_name in (vector_store.aliases)

    assert info.chunks_count > 0

    build_embedding_calls = len(
        embeddings.calls,
    )

    assert build_embedding_calls > 0

    warm = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    assert warm.cache_hit is True

    assert warm.context_id == initial.context_id

    assert warm.cache_key == initial.cache_key

    assert warm.validation == validation()

    warm_info = await create.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
        cache_key=warm.cache_key,
        validation=None,
    )

    assert warm_info.cache_hit is True

    assert (
        len(
            embeddings.calls,
        )
        == build_embedding_calls
    )


@pytest.mark.asyncio
async def test_project_context_content_change_forces_cache_miss() -> None:
    """Изменение текста ПЗ создаёт другую cache identity."""
    vector_store = FakeVectorStore()

    resolve = resolver(
        vector_store,
    )

    first = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(
            suffix="версия A",
        ),
    )

    second = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(
            suffix="версия B",
        ),
    )

    assert first.cache_key != second.cache_key

    assert first.context_id != second.context_id

    assert first.cache_hit is False

    assert second.cache_hit is False


@pytest.mark.asyncio
async def test_partial_project_context_collection_is_not_cache_hit() -> None:
    """Alias без ready metadata никогда не считается warm cache."""
    vector_store = FakeVectorStore()

    resolve = resolver(
        vector_store,
    )

    expected = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    assert expected.cache_key

    alias = expected.collection_name

    assert alias is not None

    physical = f"{alias}_broken"

    vector_store.collections[physical] = [
        VectorRecord(
            point_id="broken",
            vector=[
                1.0,
                0.5,
                0.25,
            ],
            payload={
                "_pdrd_project_context_cache_key": (expected.cache_key),
                "_pdrd_project_context_cache_ready": False,
            },
        )
    ]

    vector_store.aliases[alias] = physical

    result = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    assert result.cache_hit is False

    assert result.validation is None


@pytest.mark.asyncio
async def test_search_uses_published_project_context_alias() -> None:
    """Semantic search работает через published stable alias."""
    embeddings = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    resolve = resolver(
        vector_store,
    )

    cache = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    create = creator(
        embeddings,
        vector_store,
    )

    info = await create.execute(
        context_id=cache.context_id,
        enabled=True,
        pages=pages(),
        cache_key=cache.cache_key,
        validation=validation(),
    )

    search = SearchProjectContext(
        embedding_provider=embeddings,  # type: ignore[arg-type]
        vector_store=vector_store,  # type: ignore[arg-type]
        collection_prefix=("pdrd_project_context"),
        embedding_model=("test-embedding"),
        top_k=5,
    )

    result = await search.execute(
        context_id=info.context_id,
        enabled=True,
        query=("Оборудование проекта"),
    )

    assert (
        len(
            result.sources,
        )
        == 1
    )

    assert result.sources[0].source_id == "PZ1"

    assert embeddings.instructions[-1] is not None


@pytest.mark.asyncio
async def test_explicit_delete_removes_published_cache() -> None:
    """Explicit cleanup удаляет cache target."""
    embeddings = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    resolve = resolver(
        vector_store,
    )

    cache = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    create = creator(
        embeddings,
        vector_store,
    )

    info = await create.execute(
        context_id=cache.context_id,
        enabled=True,
        pages=pages(),
        cache_key=cache.cache_key,
        validation=validation(),
    )

    delete = DeleteProjectContext(
        vector_store=vector_store,  # type: ignore[arg-type]
        collection_prefix=("pdrd_project_context"),
    )

    assert (
        await delete.execute(
            context_id=info.context_id,
        )
        is True
    )

    after_delete = await resolve.execute(
        context_id=uuid4(),
        enabled=True,
        pages=pages(),
    )

    assert after_delete.cache_hit is False
