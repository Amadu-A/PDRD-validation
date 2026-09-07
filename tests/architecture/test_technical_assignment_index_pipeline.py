# tests/architecture/test_technical_assignment_index_pipeline.py

"""Architecture guards TZ-3 multimodal pipeline."""

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
    """T heavy work нельзя подобрать normative worker."""
    compose = (ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    assert "pdrd.knowledge.technical-assignment" in compose

    assert "technical-assignment-indexer" in compose

    assert "--concurrency=1" in compose

    assert "--queues=pdrd.knowledge.indexing" in compose

    assert "--queues=pdrd.knowledge.technical-assignment" in compose


def test_multimodal_model_has_dedicated_collection() -> None:
    """T vectors не смешиваются с qwen3-embedding vectors."""
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

    assert '"Qwen/Qwen3-VL-Embedding-8B"' in settings

    assert '"dva_multimodal_v1"' in settings

    assert '"dva_normative_v2"' in settings


def test_analysis_waits_for_t_before_n8n() -> None:
    """Analysis orchestration имеет explicit READY barrier."""
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

    wait_position = use_case.find("_ensure_technical_assignment_ready(")

    orchestrator_position = use_case.find(
        "self.orchestrator.execute(",
    )

    assert wait_position >= 0

    assert orchestrator_position > wait_position


def test_gpu_runtime_is_bounded_and_lazy() -> None:
    """Checkpoint не должен жить в обычном quality environment."""
    pyproject = (
        ROOT / "services" / "multimodal-embedding-service" / "pyproject.toml"
    ).read_text(
        encoding="utf-8",
    )

    runtime = (
        ROOT
        / "services"
        / "multimodal-embedding-service"
        / "src"
        / "pdrd_multimodal_embedding_service"
        / "runtime.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "[project.optional-dependencies]" in pyproject

    assert "gpu = [" in pyproject

    assert (
        "torch"
        not in pyproject.split(
            "gpu = [",
            maxsplit=1,
        )[0]
    )

    assert "max_concurrency" in runtime

    assert "_ensure_model_sync" in runtime

    assert "_release_sync" in runtime
