# tests/architecture/test_shared_vllm_integration.py

"""Architecture guards shared vLLM and shared embedding integration."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

ENV_EXAMPLE = ROOT / ".env.example"

ANALYSIS_CONTAINER = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "core"
    / "container.py"
)

ANALYSIS_MAIN = (
    ROOT / "services" / "analysis-service" / "src" / "pdrd_analysis_service" / "main.py"
)

VLLM_ADAPTER = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "infrastructure"
    / "vllm.py"
)

CHECK_STACK = ROOT / "scripts" / "check-stack.sh"


def test_analysis_service_uses_shared_vllm_logical_contract() -> None:
    """Composition root подключает client adapter к stable shared-vlm."""
    container = ANALYSIS_CONTAINER.read_text(
        encoding="utf-8",
    )

    adapter = VLLM_ADAPTER.read_text(
        encoding="utf-8",
    )

    assert "VllmStructuredVisionModel" in container

    assert "settings.vlm.base_url" in container

    assert "settings.vlm.model" in container

    assert "/chat/completions" in adapter

    assert "/models" in adapter

    assert '"image_url"' in adapter

    assert '"response_format"' in adapter


def test_analysis_service_does_not_manage_shared_model_residency() -> None:
    """Shared vLLM, а не PDRD, владеет model/GPU lifecycle."""
    container = ANALYSIS_CONTAINER.read_text(
        encoding="utf-8",
    )

    main = ANALYSIS_MAIN.read_text(
        encoding="utf-8",
    )

    forbidden_container_markers = (
        "OllamaStructuredVisionModel",
        "CrossProcessFileGpuLease",
        "HttpGpuMemoryProbe",
        "WaitingGpuCoordinator",
        "settings.gpu",
    )

    violations = [
        marker for marker in (forbidden_container_markers) if marker in container
    ]

    assert not violations, "\n".join(
        violations,
    )

    assert "vision_model_residency" not in main

    assert "residency_scope" not in main


def test_committed_vlm_config_contains_no_physical_model_or_gpu_layout() -> None:
    """Business config знает alias, но не checkpoint/TP/DP/GPU IDs."""
    source = ENV_EXAMPLE.read_text(
        encoding="utf-8",
    )

    required = (
        ("ANALYSIS_SERVICE_VLM__BASE_URL=http://shared-vlm:8000/v1"),
        ("ANALYSIS_SERVICE_VLM__MODEL=shared-vlm"),
    )

    missing = [marker for marker in required if marker not in source]

    assert not missing, "\n".join(
        missing,
    )

    forbidden = (
        "Qwen/Qwen3.8-27B",
        "SHARED_VLM_TP_SIZE",
        "SHARED_VLM_DP_SIZE",
        "SHARED_VLM_GPU_DEVICES",
        "ANALYSIS_SERVICE_GPU__",
        "ANALYSIS_SERVICE_VISION__KEEP_ALIVE",
        "ANALYSIS_SERVICE_VISION__NUM_CTX",
        "ANALYSIS_SERVICE_VISION__MIN_FREE_VRAM_GIB",
    )

    violations = [marker for marker in forbidden if marker in source]

    assert not violations, "\n".join(
        violations,
    )


def test_embedding_contract_uses_shared_resident_runtime() -> None:
    """Embedding identity фиксирует shared alias и schema-v2 runtime."""
    source = ENV_EXAMPLE.read_text(
        encoding="utf-8",
    )

    required = (
        "PDRD_EMBEDDING_MODEL=shared-embedding",
        "PDRD_EMBEDDING_DIMENSION=4096",
        "PDRD_EMBEDDING_SCHEMA_VERSION=2",
        ("PDRD_SHARED_EMBEDDING_BASE_URL=http://shared-embedding:8000/v1"),
        ("KNOWLEDGE_SERVICE_EMBEDDING__BASE_URL=http://shared-embedding:8000/v1"),
        (
            "KNOWLEDGE_SERVICE_MULTIMODAL_EMBEDDING__BASE_URL="
            "http://shared-embedding:8000/v1"
        ),
    )

    missing = [marker for marker in required if marker not in source]

    assert not missing, "\n".join(
        missing,
    )

    assert "PDRD_EMBEDDING_MODEL=Qwen/" not in source


def test_stack_check_probes_shared_models_from_application_containers() -> None:
    """Runtime check использует Docker DNS shared services."""
    source = CHECK_STACK.read_text(
        encoding="utf-8",
    )

    assert "http://shared-vlm:8000/health" in source

    assert "http://shared-vlm:8000/v1/models" in source

    assert "Analysis Service -> shared-vlm" in source

    assert "http://shared-embedding:8000/health" in source

    assert "http://shared-embedding:8000/v1/models" in source

    assert "Knowledge Service -> shared-embedding" in source

    assert "http://ollama:11434" not in source
