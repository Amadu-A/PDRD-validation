# tests/architecture/test_technical_assignment_index_pipeline.py

"""Architecture guards TZ-3/TZ-6 multimodal pipeline."""

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
    """T 8B vectors не смешиваются с text или legacy 2B vectors."""
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

    assert '"dva_multimodal_qwen3vl8b_v1"' in settings

    assert '"dva_normative_v2"' in settings

    assert '"dva_multimodal_qwen3vl2b_v1"' not in settings


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

    wait_position = use_case.find(
        "_ensure_technical_assignment_ready(",
    )

    orchestrator_position = use_case.find(
        "self.orchestrator.execute(",
    )

    assert wait_position >= 0

    assert orchestrator_position > wait_position


def test_t_releases_gpu_before_ready() -> None:
    """READY нельзя публиковать до explicit GPU release."""
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


def test_gpu_runtime_is_bounded_lazy_and_idle_released() -> None:
    """Checkpoint lazy-loaded и имеет bounded idle fallback."""
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

    settings = (
        ROOT
        / "services"
        / "multimodal-embedding-service"
        / "src"
        / "pdrd_multimodal_embedding_service"
        / "settings.py"
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

    assert "_release_after_idle" in runtime

    assert "idle_release_seconds" in settings

    assert "default=60.0" in settings


def test_gpu_loader_uses_bounded_direct_dispatch() -> None:
    """Большой checkpoint нельзя сначала материализовать целиком в RAM."""
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

    assert '"accelerate==1.14.0"' in pyproject

    assert '"device_map": "cuda:0"' in runtime

    assert '"low_cpu_mem_usage": True' in runtime

    assert '"dtype": model_dtype' in runtime

    assert '"torch_dtype": model_dtype' not in runtime


def test_gpu_image_runtime_dependencies_are_pinned() -> None:
    """Vision processor должен иметь reproducible CUDA dependencies."""
    pyproject = (
        ROOT / "services" / "multimodal-embedding-service" / "pyproject.toml"
    ).read_text(
        encoding="utf-8",
    )

    dockerfile = (
        ROOT / "services" / "multimodal-embedding-service" / "Dockerfile"
    ).read_text(
        encoding="utf-8",
    )

    assert '"torchvision==0.23.0"' in pyproject

    assert '"transformers==5.16.1"' in pyproject

    assert "torch==2.8.0" in dockerfile

    assert "torchvision==0.23.0" in dockerfile

    assert "https://download.pytorch.org/whl/cu128" in dockerfile


def test_knowledge_runtime_prepares_t_storage_permissions() -> None:
    """Non-root Knowledge runtime должен владеть T storage."""
    dockerfile = (ROOT / "services" / "knowledge-service" / "Dockerfile").read_text(
        encoding="utf-8",
    )

    assert "/data/normative" in dockerfile

    assert "/data/technical-assignments" in dockerfile

    assert "chown -R app:app" in dockerfile
