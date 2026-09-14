# tests/architecture/test_analysis_efficiency_configuration.py

"""Architecture guards performance configuration Analysis Service."""

from pathlib import Path

from pdrd_analysis_service.core.settings import (
    PipelineSettings,
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

OLLAMA_ADAPTER = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "infrastructure"
    / "ollama.py"
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


def test_vlm_keep_alive_supports_bounded_residency() -> None:
    """Ollama не unloads model между calls одного bounded stage."""
    assert (
        _env_value(
            "ANALYSIS_SERVICE_VISION__KEEP_ALIVE",
        )
        == "60s"
    )

    source = OLLAMA_ADAPTER.read_text(
        encoding="utf-8",
    )

    assert "async def residency_scope(" in source

    assert "self._residency_depth" in source

    assert "await self._unload_model()" in source

    assert '"keep_alive": self._keep_alive' in source


def test_all_vlm_http_stages_are_request_scoped() -> None:
    """Каждый VLM HTTP stage проходит через bounded residency middleware."""
    source = ANALYSIS_MAIN.read_text(
        encoding="utf-8",
    )

    required_paths = (
        "/internal/v1/pages/understand",
        "/internal/v1/pages/check-norms",
        "/internal/v1/pages/check-technical-assignment",
        "/internal/v1/project-context/validate",
        "/internal/v1/findings/finalize",
        "/internal/v1/findings/localize",
    )

    missing = [path for path in required_paths if path not in source]

    assert not missing, "\n".join(
        missing,
    )

    assert "vision_model_residency(" in source
