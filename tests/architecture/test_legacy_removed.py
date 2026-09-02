# tests/architecture/test_legacy_removed.py

"""Architecture guards окончательного удаления legacy runtime."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

LEGACY_PATHS = (
    REPOSITORY_ROOT / "services" / "pdf-service",
    REPOSITORY_ROOT / "n8n" / "workflows" / "analysis-main.json",
    REPOSITORY_ROOT / "scripts" / "check-stage2.sh",
)

FILES_WITHOUT_LEGACY_REFERENCES = (
    REPOSITORY_ROOT / "compose.yaml",
    REPOSITORY_ROOT / ".env.example",
    REPOSITORY_ROOT / "pyproject.toml",
    REPOSITORY_ROOT / "scripts" / "check-stack.sh",
)

FORBIDDEN_MARKERS = (
    "services/pdf-service",
    "pdrd-pdf-service",
    "PDF_SERVICE_PORT",
    "analysis-main.json",
    ":8101",
)


def test_legacy_paths_are_removed() -> None:
    """Не допускает возврат удалённого legacy runtime."""
    existing = [
        path.relative_to(
            REPOSITORY_ROOT,
        ).as_posix()
        for path in LEGACY_PATHS
        if path.exists()
    ]

    assert not existing, "Найдены удалённые legacy paths:\n" + "\n".join(existing)


def test_runtime_configuration_has_no_legacy_references() -> None:
    """Проверяет compose/env/quality/runtime scripts."""
    violations: list[str] = []

    for path in FILES_WITHOUT_LEGACY_REFERENCES:
        content = path.read_text(
            encoding="utf-8",
        )

        for marker in FORBIDDEN_MARKERS:
            if marker not in content:
                continue

            violations.append(
                path.relative_to(
                    REPOSITORY_ROOT,
                ).as_posix()
                + f": {marker}"
            )

    assert not violations, "\n".join(
        violations,
    )


def test_only_v2_analysis_workflows_remain() -> None:
    """Фиксирует V2 workflow set после cutover."""
    workflows_dir = REPOSITORY_ROOT / "n8n" / "workflows"

    actual = {
        path.name
        for path in workflows_dir.glob(
            "*.json",
        )
    }

    expected = {
        "analysis-v2-pdf.json",
        "analysis-v2-cad.json",
        "analysis-v2-pdf-cad.json",
    }

    assert actual == expected


def test_frontend_is_not_connected_to_shared_network() -> None:
    """Browser-facing frontend не должен видеть shared AI network."""
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text(
        encoding="utf-8",
    )

    frontend_block = compose.split(
        "\n  frontend:\n",
        maxsplit=1,
    )[1].split(
        "\nvolumes:\n",
        maxsplit=1,
    )[0]

    assert "ai-shared" not in frontend_block
    assert "N8N_BASE_URL" not in frontend_block
