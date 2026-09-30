# tests/architecture/test_quality_image.py

"""Architecture tests server-side quality Docker image.

Модуль закрепляет полный repository context для quality container.
Architecture tests внутри Docker должны видеть frontend, n8n, runtime scripts
и корневые configuration files, при этом тяжёлые runtime data не должны
попадать в build context.

Также тесты контролируют подготовку локальных editable dependencies до
запуска pip install.
"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

QUALITY_DOCKERFILE = REPOSITORY_ROOT / "ops" / "Dockerfile.quality"

QUALITY_DOCKERIGNORE = REPOSITORY_ROOT / "ops" / "Dockerfile.quality.dockerignore"

USER_SERVICE_DOCKERFILE = REPOSITORY_ROOT / "services" / "user-service" / "Dockerfile"

USER_SERVICE_DOCKERIGNORE = (
    REPOSITORY_ROOT / "services" / "user-service" / ".dockerignore"
)

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


def test_quality_dockerfile_prepares_editable_services_before_pip() -> None:
    """Требует наличие локальных сервисов до установки editable packages."""
    content = QUALITY_DOCKERFILE.read_text(
        encoding="utf-8",
    )

    services_position = content.find(
        "COPY services /workspace/services",
    )

    pip_install_position = content.find(
        "RUN pip install",
    )

    repository_position = content.find(
        "COPY . /workspace",
    )

    assert services_position >= 0
    assert pip_install_position >= 0
    assert repository_position >= 0

    assert services_position < pip_install_position < repository_position


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
    assert ".codex-test-temp/" in ignore_patterns


def test_user_service_image_packages_migrations_and_runs_unprivileged() -> None:
    """Runtime image содержит свой Alembic и не запускает приложение от root."""
    content = USER_SERVICE_DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY pyproject.toml alembic.ini /app/" in content
    assert "COPY alembic /app/alembic" in content
    assert "COPY src /app/src" in content
    assert "pip install --no-cache-dir ." in content
    assert "USER app" in content
    assert "pdrd_user_service.main:app" in content


def test_user_service_build_context_excludes_secrets_and_test_artifacts() -> None:
    """Сервисный Docker build не отправляет локальные секреты и тестовые данные."""
    patterns = {
        line.strip()
        for line in USER_SERVICE_DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {".env", ".env.*", "tests/", ".codex-test-temp/"} <= patterns
