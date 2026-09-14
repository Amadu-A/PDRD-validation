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
    """Deployment baseline должен объединять до 10 findings в один VLM call."""
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
