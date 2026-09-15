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


def test_one_command_startup_script_exists() -> None:
    """Repository содержит единый startup entrypoint."""
    assert UP_SCRIPT.is_file()


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


def test_operational_shell_scripts_have_real_shebang() -> None:
    """Shell entrypoints начинают файл с executable shebang."""
    for path in (
        UP_SCRIPT,
        CHECK_STACK_SCRIPT,
    ):
        first_line = path.read_text(
            encoding="utf-8",
        ).splitlines()[0]

        assert first_line == "#!/usr/bin/env bash"
