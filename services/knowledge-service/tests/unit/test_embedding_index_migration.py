# services/knowledge-service/tests/unit/test_embedding_index_migration.py

"""Unit tests safe embedding blue/green migration."""

import pytest
from pdrd_knowledge_service.application.use_cases.embedding_migration import (
    MigrateEmbeddingIndexes,
)
from pdrd_knowledge_service.domain.embedding_index import (
    EmbeddingIdentity,
    EmbeddingIndexPlan,
)


class FakeRebuilder:
    """Фиксирует rebuild calls."""

    def __init__(
        self,
        *,
        fail_technical_assignment: bool = False,
    ) -> None:
        """Сохраняет controlled migration behaviour."""
        self.calls: list[
            tuple[
                str,
                str,
            ]
        ] = []

        self._fail_technical_assignment = fail_technical_assignment

    async def rebuild_catalog(
        self,
        *,
        collection: str,
    ) -> int:
        """Имитирует catalog rebuild."""
        self.calls.append(
            (
                "catalog",
                collection,
            )
        )

        return 10

    async def rebuild_technical_assignments(
        self,
        *,
        collection: str,
    ) -> int:
        """Имитирует T rebuild."""
        self.calls.append(
            (
                "technical_assignment",
                collection,
            )
        )

        if self._fail_technical_assignment:
            raise RuntimeError(
                "simulated migration failure",
            )

        return 5

    async def rebuild_experience(
        self,
        *,
        collection: str,
        source_collection: str | None,
    ) -> int:
        """Имитирует experience rebuild."""
        self.calls.append(
            (
                "experience",
                collection,
            )
        )

        assert source_collection in {
            "legacy_experience",
            "dva_experience_v2",
            None,
        }

        return 3


class FakeVectorStore:
    """In-memory Qdrant admin fake."""

    def __init__(
        self,
    ) -> None:
        """Создаёт initial legacy collections и aliases."""
        self.collections = {
            "legacy_catalog",
            "legacy_t",
            "legacy_experience",
        }

        self.aliases = {
            "catalog_active": "legacy_catalog",
            "t_active": "legacy_t",
            "experience_active": "legacy_experience",
        }

        self.deleted: list[str] = []

    async def get_alias_target(
        self,
        alias: str,
    ) -> str | None:
        """Возвращает fake alias target."""
        return self.aliases.get(
            alias,
        )

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет fake physical collection."""
        return collection in self.collections

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт fake collection."""
        assert vector_size == 4096

        self.collections.add(
            collection,
        )

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Удаляет fake collection."""
        existed = collection in self.collections

        self.collections.discard(
            collection,
        )

        self.deleted.append(
            collection,
        )

        return existed

    async def replace_aliases(
        self,
        aliases_to_targets: dict[str, str],
    ) -> None:
        """Переключает fake aliases."""
        self.aliases.update(
            aliases_to_targets,
        )

    async def list_collections(
        self,
    ) -> tuple[str, ...]:
        """Возвращает fake physical collections."""
        return tuple(
            self.collections,
        )


def _plan() -> EmbeddingIndexPlan:
    """Создаёт deterministic plan."""
    return EmbeddingIndexPlan(
        identity=EmbeddingIdentity(
            model="Qwen/Qwen3-VL-Embedding-8B",
            dimension=4096,
            schema_version=1,
        ),
        catalog_alias="catalog_active",
        technical_assignment_alias="t_active",
        experience_alias="experience_active",
        catalog_prefix="catalog",
        technical_assignment_prefix="t",
        experience_prefix="experience",
    )


async def test_successful_migration_switches_all_aliases_then_cleans_old() -> None:
    """Cutover выполняется только после успешных rebuilds."""
    vector_store = FakeVectorStore()

    migrator = MigrateEmbeddingIndexes(
        vector_store=vector_store,
        rebuilder=FakeRebuilder(),
        plan=_plan(),
        vector_size=4096,
        legacy_collections=(
            "legacy_catalog",
            "legacy_t",
            "legacy_experience",
        ),
    )

    result = await migrator.execute()

    assert result.skipped is False

    assert result.catalog_points == 10

    assert result.technical_assignment_points == 5

    assert result.experience_points == 3

    plan = _plan()

    assert vector_store.aliases == {
        "catalog_active": plan.catalog_target,
        "t_active": (plan.technical_assignment_target),
        "experience_active": (plan.experience_target),
    }

    assert "legacy_catalog" not in vector_store.collections

    assert "legacy_t" not in vector_store.collections

    assert "legacy_experience" not in vector_store.collections


async def test_failed_migration_never_moves_existing_aliases() -> None:
    """Partial rebuild не разрушает рабочий old vector space."""
    vector_store = FakeVectorStore()

    old_aliases = dict(
        vector_store.aliases,
    )

    migrator = MigrateEmbeddingIndexes(
        vector_store=vector_store,
        rebuilder=FakeRebuilder(
            fail_technical_assignment=True,
        ),
        plan=_plan(),
        vector_size=4096,
        legacy_collections=(
            "legacy_catalog",
            "legacy_t",
            "legacy_experience",
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated migration failure",
    ):
        await migrator.execute()

    assert vector_store.aliases == old_aliases

    assert "legacy_catalog" in vector_store.collections

    assert "legacy_t" in vector_store.collections

    assert "legacy_experience" in vector_store.collections


async def test_same_embedding_identity_is_fast_noop() -> None:
    """Container recreation не переиндексирует всё без model change."""
    vector_store = FakeVectorStore()

    plan = _plan()

    vector_store.collections = set(plan.aliases_to_targets.values())

    vector_store.aliases = dict(
        plan.aliases_to_targets,
    )

    rebuilder = FakeRebuilder()

    result = await MigrateEmbeddingIndexes(
        vector_store=vector_store,
        rebuilder=rebuilder,
        plan=plan,
        vector_size=4096,
        legacy_collections=(),
    ).execute()

    assert result.skipped is True

    assert rebuilder.calls == []
