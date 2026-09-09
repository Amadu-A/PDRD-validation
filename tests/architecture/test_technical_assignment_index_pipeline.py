# tests/architecture/test_technical_assignment_index_pipeline.py

"""Architecture guards unified embedding T pipeline."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)


def test_t_outbox_is_not_normative_outbox() -> None:
    """T aggregate имеет собственную таблицу и FK."""
    migration = (
        ROOT
        / "services"
        / "knowledge-service"
        / "alembic"
        / "versions"
        / "20260905_0004_create_technical_assignments.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "technical_assignment_outbox_messages" in migration

    assert "technical_assignments.id" in migration

    assert "normative_documents.id" not in migration


def test_t_index_uses_separate_queue_and_worker() -> None:
    """T heavy work не забирает normative worker."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert "pdrd.knowledge.technical-assignment" in compose

    assert "technical-assignment-indexer" in compose

    assert "--concurrency=1" in compose

    assert "--prefetch-multiplier=1" in compose


def test_knowledge_workers_can_reach_shared_rabbitmq() -> None:
    """Оба indexing worker обязаны состоять в shared RabbitMQ network."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    knowledge_block = compose.split(
        "  knowledge-indexer:",
        maxsplit=1,
    )[1].split(
        "  technical-assignment-indexer:",
        maxsplit=1,
    )[0]

    technical_block = compose.split(
        "  technical-assignment-indexer:",
        maxsplit=1,
    )[1].split(
        "  knowledge-service-tests:",
        maxsplit=1,
    )[0]

    assert "ai-shared" in knowledge_block

    assert "ai-shared" in technical_block


def test_t_uses_same_embedding_identity_but_separate_collection() -> None:
    """T и text share model, но не смешивают payload vector spaces."""
    settings = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "core"
        / "settings.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "synchronize_embedding_identity" in settings

    assert '"dva_catalog_active"' in settings

    assert '"dva_technical_assignment_active"' in settings

    assert '"dva_experience_active"' in settings

    assert "qwen3-embedding:4b" not in settings


def test_analysis_waits_for_t_before_n8n() -> None:
    """Analysis orchestration сохраняет READY barrier."""
    use_case = (
        ROOT
        / "services"
        / "api-gateway"
        / "src"
        / "pdrd_api_gateway"
        / "application"
        / "use_cases"
        / "execute_analysis_job.py"
    ).read_text(
        encoding="utf-8",
    )

    wait_position = use_case.find(
        "_ensure_technical_assignment_ready(",
    )

    orchestrator_position = use_case.find(
        "self.orchestrator.execute(",
    )

    assert wait_position >= 0

    assert orchestrator_position > wait_position


def test_gateway_passes_t_id_to_every_source_mode() -> None:
    """T ID формируется до выбора PDF/CAD/PDF+CAD webhook."""
    orchestrator = (
        ROOT
        / "services"
        / "api-gateway"
        / "src"
        / "pdrd_api_gateway"
        / "infrastructure"
        / "orchestration"
        / "n8n.py"
    ).read_text(
        encoding="utf-8",
    )

    assert 'data["technical_assignment_id"]' in orchestrator

    assert "AnalysisSourceMode.PDF_ONLY" in orchestrator

    assert "AnalysisSourceMode.CAD_ONLY" in orchestrator

    assert "AnalysisSourceMode.PDF_CAD" in orchestrator


def test_t_releases_gpu_before_ready() -> None:
    """READY нельзя публиковать до embedding release."""
    use_case = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "application"
        / "use_cases"
        / "index_technical_assignment.py"
    ).read_text(
        encoding="utf-8",
    )

    release_position = use_case.find(
        "await self.embedding_provider.release()",
    )

    ready_position = use_case.find(
        "return await self._mark_ready(",
    )

    assert release_position >= 0

    assert ready_position > release_position


def test_gpu_coordination_is_cross_container() -> None:
    """Analysis и embedding service монтируют один OS lock."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert (
        compose.count(
            "gpu_coordination:/var/lock/pdrd-gpu",
        )
        == 2
    )

    assert "gpu_coordination:" in compose


def test_embedding_migration_gates_knowledge_runtime() -> None:
    """Search/index workers не стартуют до safe reindex cutover."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert "knowledge-embedding-migrator:" in compose

    assert "service_completed_successfully" in compose
