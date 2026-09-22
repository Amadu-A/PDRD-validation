# tests/architecture/test_operational_startup.py

"""Architecture guards воспроизводимого запуска PDRD stack."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

UP_SCRIPT = ROOT / "scripts" / "up.sh"

CHECK_STACK_SCRIPT = ROOT / "scripts" / "check-stack.sh"

EMBEDDING_MIGRATION_SCRIPT = ROOT / "scripts" / "migrate-embedding-indexes.sh"


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
    """One-command startup поднимает shared stack и применяет migrations."""
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
