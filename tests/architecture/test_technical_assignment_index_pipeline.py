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
    """Оба indexing worker обязаны состоять в shared network."""
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


def test_t_release_is_currently_compatibility_noop_before_ready() -> None:
    """До cleanup-этапа T lifecycle сохраняет harmless compatibility call."""
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

    adapter = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "infrastructure"
        / "embedding"
        / "multimodal_http.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "await self.embedding_provider.release()" in use_case

    assert "Compatibility no-op" in adapter

    assert "/internal/v1/release" not in adapter


def test_project_compose_uses_shared_embedding_without_local_runtime() -> None:
    """Main stack не запускает project-local embedding GPU runtime."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert "\n  multimodal-embedding-service:\n" not in compose

    assert "pdrd-multimodal-embedding-service" not in compose

    assert "multimodal_model_cache:" not in compose

    assert "gpu_coordination:" not in compose

    assert "http://shared-embedding:8000/v1" in compose

    migrator_block = compose.split(
        "  knowledge-embedding-migrator:",
        maxsplit=1,
    )[1].split(
        "  knowledge-service:",
        maxsplit=1,
    )[0]

    assert "ai-shared" in migrator_block

    assert (ROOT / "services" / "multimodal-embedding-service").is_dir()


def test_embedding_migration_preserves_atomic_t_requirements() -> None:
    """Schema migration rebuild-ит page и requirement representations."""
    migration = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "application"
        / "use_cases"
        / "embedding_migration.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "extract_technical_assignment_requirements" in migration

    assert '"requirement_text"' in migration

    assert "_T_REQUIREMENT_EMBEDDING_INSTRUCTION" in migration

    assert "parent_page_point_id" in migration


def test_embedding_migration_gates_knowledge_runtime() -> None:
    """Search/index workers не стартуют до safe reindex cutover."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert "knowledge-embedding-migrator:" in compose

    assert "service_completed_successfully" in compose
