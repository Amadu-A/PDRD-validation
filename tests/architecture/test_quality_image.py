# tests/architecture/test_quality_image.py

"""Architecture tests server-side quality Docker image.

Модуль закрепляет полный repository context для quality container.
Architecture tests внутри Docker должны видеть frontend, n8n, runtime scripts
и корневые configuration files, при этом тяжёлые runtime data не должны
попадать в build context.
"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

QUALITY_DOCKERFILE = REPOSITORY_ROOT / "ops" / "Dockerfile.quality"

QUALITY_DOCKERIGNORE = REPOSITORY_ROOT / "ops" / "Dockerfile.quality.dockerignore"

REQUIRED_CONTEXT_PATHS = {
    "frontend/",
    "n8n/",
    "scripts/",
    "compose.yaml",
    ".env.example",
}


def read_ignore_patterns() -> set[str]:
    """Возвращает активные patterns quality-specific dockerignore."""
    return {
        line.strip()
        for line in QUALITY_DOCKERIGNORE.read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_quality_dockerfile_copies_repository() -> None:
    """Закрепляет полный repository snapshot внутри quality image."""
    content = QUALITY_DOCKERFILE.read_text(
        encoding="utf-8",
    )

    assert "COPY . /workspace" in content


def test_quality_context_keeps_architecture_inputs() -> None:
    """Не позволяет исключить файлы, читаемые architecture tests."""
    ignore_patterns = read_ignore_patterns()

    excluded = sorted(REQUIRED_CONTEXT_PATHS & ignore_patterns)

    assert not excluded, (
        "Quality Docker context исключает обязательные "
        "repository paths:\n"
        + "\n".join(
            excluded,
        )
    )


def test_quality_context_ignores_runtime_data() -> None:
    """Не отправляет тяжёлый каталог runtime data в quality build."""
    ignore_patterns = read_ignore_patterns()

    assert "data/" in ignore_patterns
