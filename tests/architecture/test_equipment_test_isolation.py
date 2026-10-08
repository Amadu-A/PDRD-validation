# tests/architecture/test_equipment_test_isolation.py

"""Контролирует изоляцию настоящих Equipment DB тестов от рабочего стека."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_equipment_database_stack_is_isolated_and_migrates_before_tests() -> None:
    """Тестовые данные живут только в tmpfs без портов и внешних сетей."""
    stack = yaml.safe_load(
        (ROOT / "ops/compose.equipment-test.yaml").read_text(encoding="utf-8")
    )
    database = stack["services"]["equipment-test-postgres"]
    runner = stack["services"]["equipment-test-runner"]
    assert "ports" not in database and "volumes" not in database
    assert database["tmpfs"] == ["/var/lib/postgresql/data"]
    assert all(
        network.get("internal") and not network.get("external")
        for network in stack["networks"].values()
    )
    assert runner["environment"]["PDRD_RUN_DATABASE_TESTS"] == "1"
    assert (
        runner["environment"]["EQUIPMENT_SEARCH_SERVICE_DATABASE__NAME"]
        == "pdrd_equipment_test"
    )
    commands = "\n".join(runner["command"])
    assert commands.index("alembic.ini upgrade head") < commands.index("pytest")
    assert "services/equipment-search-service/tests" in commands
    quality = (ROOT / "ops/check-quality.sh").read_text(encoding="utf-8")
    assert (
        "for suite in gateway knowledge experience user auth equipment; do" in quality
    )
    assert "ops/compose.${suite}-test.yaml" in quality
