# tests/architecture/test_operational_startup.py

"""Architecture guards воспроизводимого запуска PDRD stack."""

from pathlib import Path

import yaml

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

UP_SCRIPT = ROOT / "scripts" / "up.sh"

CHECK_STACK_SCRIPT = ROOT / "scripts" / "check-stack.sh"

STACK_PROFILES_HELPER = ROOT / "scripts" / "lib" / "stack-profiles.sh"

EMBEDDING_MIGRATION_SCRIPT = ROOT / "scripts" / "migrate-embedding-indexes.sh"

USER_TEST_COMPOSE = ROOT / "ops" / "compose.user-test.yaml"


def test_one_command_startup_script_exists() -> None:
    """Repository содержит единый startup entrypoint."""
    assert UP_SCRIPT.is_file()


def test_embedding_cutover_script_exists() -> None:
    """Repository содержит controlled embedding migration entrypoint."""
    assert EMBEDDING_MIGRATION_SCRIPT.is_file()


def test_startup_provisions_project_rabbitmq_namespace() -> None:
    """Startup сам восстанавливает PDRD user/vhost в shared RabbitMQ."""
    source = UP_SCRIPT.read_text(
        encoding="utf-8",
    )

    required_markers = (
        "list_vhosts",
        "add_vhost",
        "list_users",
        "add_user",
        "authenticate_user",
        "change_password",
        "set_permissions",
        "PDRD_RABBITMQ_USER",
        "PDRD_RABBITMQ_VHOST",
        "PDRD_RABBITMQ_PASSWORD",
    )

    missing = [marker for marker in required_markers if marker not in source]

    assert not missing, "\n".join(
        missing,
    )


def test_startup_knows_real_shared_repository_locations() -> None:
    """Startup знает фактические варианты расположения shared repository."""
    source = UP_SCRIPT.read_text(
        encoding="utf-8",
    )

    assert "/projects/shared-infrastructure" in source

    assert "${HOME}/projects/shared-infrastructure" in source


def test_startup_runs_shared_bootstrap_and_database_migrations() -> None:
    """One-command startup поднимает shared stack и применяет core migrations."""
    source = UP_SCRIPT.read_text(
        encoding="utf-8",
    )

    assert "bash scripts/bootstrap.sh" in source

    assert (
        source.count(
            "alembic upgrade head",
        )
        == 2
    )

    assert "api-gateway" in source

    assert "knowledge-service" in source

    assert "bash scripts/check-stack.sh" in source


def test_optional_experience_profiles_use_the_one_command_startup() -> None:
    """Review и индексатор подключаются только при явном выборе Compose profiles."""
    startup = UP_SCRIPT.read_text(encoding="utf-8")
    stack_check = CHECK_STACK_SCRIPT.read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "experience-migrate" in compose
    assert "service_completed_successfully" in compose
    assert "docker compose up -d --remove-orphans" in startup
    assert "--profile review" not in startup
    helper = STACK_PROFILES_HELPER.read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES" in startup and "COMPOSE_PROFILES" in helper
    assert 'profile_enabled "experience-index"' in startup
    assert "PDRD_STARTUP_TIMEOUT_SECONDS:-1200" in startup
    assert "COMPOSE_PROFILES=experience-index требует также review" in startup

    review_checks = stack_check.split("if (( review_active )); then", 1)[1].split(
        "\nfi", 1
    )[0]
    assert 'check_completed_service "experience-migrate" "review"' in review_checks
    assert 'check_service_state "experience-service" "review"' in review_checks
    assert 'check_service_state "review-frontend" "review"' in review_checks
    index_checks = stack_check.split("if (( experience_index_active )); then", 1)[
        1
    ].split("\nfi", 1)[0]
    assert 'check_service_state "experience-indexer" "experience-index"' in index_checks
    assert '"Review frontend -> API Gateway proxy"' in stack_check
    assert 'ok "Experience Service ready (internal)"' in stack_check


def test_identity_profile_runs_private_user_service_after_its_migrations() -> None:
    """User Service запускается явно, после миграции и без публикации HTTP-порта."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    migrator = services["user-migrate"]
    user_service = services["user-service"]

    assert migrator["profiles"] == ["identity"]
    assert user_service["profiles"] == ["identity"]
    assert migrator["build"]["context"] == "./services/user-service"
    assert user_service["build"]["context"] == "./services/user-service"
    assert migrator["command"] == [
        "python",
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
    ]
    assert migrator["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        user_service["depends_on"]["user-migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert "ports" not in migrator and "ports" not in user_service
    assert "env_file" not in migrator
    assert migrator["networks"] == ["app-net"]
    assert user_service["networks"] == ["app-net"]
    assert user_service["environment"]["USER_SERVICE_ENABLED"] == "true"
    assert "USER_SERVICE_INTERNAL_KEY" not in migrator["environment"]
    assert (
        user_service["environment"]["USER_SERVICE_INTERNAL_KEY"]
        == "${USER_SERVICE_INTERNAL_KEY:-}"
    )
    assert "USER_SERVICE_DATABASE__PASSWORD" in user_service["environment"]
    assert "/health/ready" in " ".join(user_service["healthcheck"]["test"])


def test_identity_profile_is_checked_by_one_command_scripts() -> None:
    """Запуск требует секрет и проверяет миграции и внутреннюю готовность сервиса."""
    startup = UP_SCRIPT.read_text(encoding="utf-8")
    stack_check = CHECK_STACK_SCRIPT.read_text(encoding="utf-8")

    assert 'if profile_enabled "identity"; then' in startup
    assert 'validate_secret "USER_SERVICE_INTERNAL_KEY"' in startup
    assert "${#USER_SERVICE_INTERNAL_KEY} < 32" in startup
    assert "log_services+=(user-migrate user-service)" in startup
    assert "--profile identity" not in startup
    identity_checks = stack_check.split("if (( identity_active )); then", 1)[1].split(
        "\nfi", 1
    )[0]
    assert 'check_completed_service "user-migrate" "identity"' in identity_checks
    assert 'check_service_state "user-service" "identity"' in identity_checks
    assert "User Service ready (internal)" in stack_check
    assert "docker compose --profile identity exec" in stack_check


def test_stack_check_reports_running_optional_services_without_profile_flag() -> None:
    """Проверка замечает уже запущенные Review и Identity контейнеры."""
    stack_check = CHECK_STACK_SCRIPT.read_text(encoding="utf-8")
    helper = STACK_PROFILES_HELPER.read_text(encoding="utf-8")

    assert 'source "${REPO_DIR}/scripts/lib/stack-profiles.sh"' in stack_check
    assert 'optional_profile_active "review"' in stack_check
    assert 'optional_profile_active "identity"' in stack_check
    assert 'optional_profile_active "experience-index"' in stack_check
    assert "docker compose --profile review exec" in stack_check
    assert "docker compose --profile identity exec" in stack_check
    assert "docker compose --profile" in helper
    assert "ps \\" in helper
    assert "--all --quiet" in helper
    assert "[SKIP] identity" in stack_check


def test_user_database_runner_is_isolated_from_project_state() -> None:
    """Интеграционные проверки используют временную БД и отдельную сеть."""
    compose = yaml.safe_load(USER_TEST_COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    database = services["user-test-postgres"]
    runner = services["user-test-runner"]

    assert compose["name"] == "pdrd-user-service-test"
    assert set(services) == {"user-test-postgres", "user-test-runner"}
    assert compose["networks"]["user-test-only"]["internal"] is True
    assert "/var/lib/postgresql/data" in database["tmpfs"]
    assert "volumes" not in compose
    for service in services.values():
        assert "ports" not in service
        assert "env_file" not in service
        assert service["networks"] == ["user-test-only"]
    assert runner["build"]["dockerfile"] == "ops/Dockerfile.quality"
    assert runner["environment"]["PDRD_RUN_DATABASE_TESTS"] == "1"
    assert (
        "user-test-postgres" in runner["environment"]["USER_SERVICE_TEST_DATABASE_URL"]
    )
    commands = "\n".join(runner["command"])
    assert "alembic -c alembic.ini upgrade head" in commands
    assert "alembic -c alembic.ini current --check-heads" in commands
    assert "services/user-service/tests/integration" in commands


def test_startup_does_not_destroy_persistent_state() -> None:
    """Startup не содержит destructive recovery операций."""
    source = UP_SCRIPT.read_text(
        encoding="utf-8",
    )

    forbidden = (
        "docker compose down -v",
        "docker volume rm",
        "rabbitmqctl delete_user",
        "rabbitmqctl delete_vhost",
        "gpu.lock",
    )

    violations = [marker for marker in forbidden if marker in source]

    assert not violations, "\n".join(
        violations,
    )


def test_stack_check_covers_background_workers() -> None:
    """Stack check контролирует worker/outbox services, а не только HTTP."""
    source = CHECK_STACK_SCRIPT.read_text(
        encoding="utf-8",
    )

    required_services = (
        "api-gateway-outbox",
        "api-gateway-worker",
        "knowledge-service-outbox",
        "knowledge-indexer",
        "technical-assignment-indexer",
        "knowledge-embedding-migrator",
    )

    missing = [service for service in required_services if service not in source]

    assert not missing, "\n".join(
        missing,
    )


def test_startup_removes_obsolete_orphan_containers_without_pruning_volumes() -> None:
    """Startup удаляет только obsolete containers, не persistent volumes."""
    for path in (
        UP_SCRIPT,
        EMBEDDING_MIGRATION_SCRIPT,
    ):
        source = path.read_text(
            encoding="utf-8",
        )

        assert "--remove-orphans" in source

        assert "docker compose down -v" not in source

        assert "docker volume rm" not in source


def test_embedding_cutover_refreshes_frontend_and_waits_for_full_readiness() -> None:
    """Cutover не проверяет stack до worker/proxy readiness."""
    source = EMBEDDING_MIGRATION_SCRIPT.read_text(
        encoding="utf-8",
    )

    required = (
        "PDRD_STARTUP_TIMEOUT_SECONDS",
        "PDRD_STARTUP_POLL_SECONDS",
        "--force-recreate",
        "frontend",
        "while true",
        "bash scripts/check-stack.sh",
    )

    missing = [marker for marker in required if marker not in source]

    assert not missing, "\n".join(
        missing,
    )


def test_embedding_cutover_is_blue_green_and_non_destructive() -> None:
    """Cutover использует migrator и не удаляет persistent Docker state."""
    source = EMBEDDING_MIGRATION_SCRIPT.read_text(
        encoding="utf-8",
    )

    required = (
        "knowledge-embedding-migrator",
        "shared-embedding",
        "embedding_preflight",
        "alias_snapshot",
        "docker compose stop",
        "docker compose up -d",
    )

    missing = [marker for marker in required if marker not in source]

    assert not missing, "\n".join(
        missing,
    )

    forbidden = (
        "docker compose down -v",
        "docker volume rm",
        "qdrant_data",
    )

    violations = [marker for marker in forbidden if marker in source]

    assert not violations, "\n".join(
        violations,
    )


def test_operational_shell_scripts_have_real_shebang() -> None:
    """Shell entrypoints начинают файл с executable shebang."""
    for path in (
        UP_SCRIPT,
        CHECK_STACK_SCRIPT,
        STACK_PROFILES_HELPER,
        EMBEDDING_MIGRATION_SCRIPT,
    ):
        first_line = path.read_text(
            encoding="utf-8",
        ).splitlines()[0]

        assert first_line == "#!/usr/bin/env bash"


def test_operational_shell_scripts_avoid_invalid_multiline_if_subshells() -> None:
    """Guard запрещает конструкцию, сломавшую Bash в commit 41aa770."""
    for path in (
        UP_SCRIPT,
        CHECK_STACK_SCRIPT,
        EMBEDDING_MIGRATION_SCRIPT,
    ):
        source = path.read_text(
            encoding="utf-8",
        )

        assert "\n    if (\n" not in source

        assert "\n        if (\n" not in source
