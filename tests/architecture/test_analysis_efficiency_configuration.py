# tests/architecture/test_analysis_efficiency_configuration.py

"""Architecture guards performance configuration Analysis Service."""

from pathlib import Path

from pdrd_analysis_service.core.settings import (
    PipelineSettings,
    VllmSettings,
)

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

ENV_EXAMPLE = ROOT / ".env.example"

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

STAGE_BATCH_ROUTES = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "transport"
    / "http"
    / "stage_batch_routes.py"
)


def _env_value(
    key: str,
) -> str:
    """Читает одно committed значение из .env.example."""
    prefix = f"{key}="

    for raw_line in ENV_EXAMPLE.read_text(
        encoding="utf-8",
    ).splitlines():
        line = raw_line.strip()

        if line.startswith(
            prefix,
        ):
            return line[
                len(
                    prefix,
                ) :
            ]

    raise AssertionError(
        f"В .env.example отсутствует {key}.",
    )


def test_finalization_default_deployment_uses_one_batch_for_ten_findings() -> None:
    """Deployment baseline объединяет до 10 findings в один VLM call."""
    assert (
        _env_value(
            "ANALYSIS_SERVICE_PIPELINE__FINAL_BATCH_SIZE",
        )
        == "10"
    )

    settings = PipelineSettings(
        final_batch_size=10,
    )

    assert settings.final_batch_size == 10


def test_finalization_output_budget_is_sufficient_for_large_batch() -> None:
    """Большой finalization batch получает запас structured output."""
    assert (
        _env_value(
            "ANALYSIS_SERVICE_PIPELINE__FINAL_NUM_PREDICT",
        )
        == "4000"
    )

    settings = PipelineSettings(
        final_num_predict=4000,
    )

    assert settings.final_num_predict == 4000


def test_shared_vlm_uses_stable_logical_contract() -> None:
    """PDRD зависит от alias shared-vlm, а не physical checkpoint."""
    assert (
        _env_value(
            "ANALYSIS_SERVICE_VLM__BASE_URL",
        )
        == "http://shared-vlm:8000/v1"
    )

    assert (
        _env_value(
            "ANALYSIS_SERVICE_VLM__MODEL",
        )
        == "shared-vlm"
    )

    settings = VllmSettings()

    assert settings.base_url == "http://shared-vlm:8000/v1"

    assert settings.model == "shared-vlm"


def test_pdf_vlm_stages_use_bounded_client_concurrency() -> None:
    """Document stages одновременно отправляют несколько requests."""
    assert (
        _env_value(
            ("ANALYSIS_SERVICE_PIPELINE__VLM_STAGE_CONCURRENCY"),
        )
        == "4"
    )

    settings = PipelineSettings()

    assert settings.vlm_stage_concurrency == 4

    source = STAGE_BATCH_ROUTES.read_text(
        encoding="utf-8",
    )

    assert "_run_stage_items(" in source
    assert "vlm_stage_concurrency" in source
    assert "asyncio.gather(" in source


def test_vllm_adapter_has_no_project_gpu_or_model_lifecycle() -> None:
    """Shared vLLM отвечает за residency, scheduling и physical GPUs."""
    source = VLLM_ADAPTER.read_text(
        encoding="utf-8",
    )

    forbidden = (
        "gpu_coordinator",
        "keep_alive",
        "_unload_model",
        "/api/chat",
        "/api/ps",
        "num_ctx",
        "min_free_vram",
    )

    violations = [marker for marker in forbidden if marker in source]

    assert not violations, "\n".join(
        violations,
    )

    assert "/chat/completions" in source

    assert '"type": "json_schema"' in source

    assert '"enable_thinking": False' in source


def test_analysis_app_has_no_request_scoped_model_residency_middleware() -> None:
    """Business application больше не управляет residency shared model."""
    source = ANALYSIS_MAIN.read_text(
        encoding="utf-8",
    )

    assert "vision_model_residency" not in source

    assert "_VLM_RESIDENCY_PATHS" not in source

    assert "vlm_residency_middleware" not in source
