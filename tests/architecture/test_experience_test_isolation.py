# tests/architecture/test_experience_test_isolation.py

"""Архитектурные ограничения изолированного тестирования Experience.

Зачем нужен файл:
- запрещает подключение тестового Compose к общей инфраструктуре;
- закрепляет отсутствие публикации PostgreSQL на хосте;
- проверяет использование временного хранилища;
- предотвращает запуск полного набора DB-тестов под тестовым флагом.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEST_COMPOSE = ROOT / "ops" / "compose.experience-test.yaml"


def test_postgres_environment_is_fully_isolated() -> None:
    """Тестовая БД не должна использовать сеть и тома рабочего проекта."""
    source = TEST_COMPOSE.read_text(
        encoding="utf-8",
    )

    required = (
        "experience-test-postgres:",
        "experience-test-runner:",
        "pdrd_experience_test",
        "experience_test",
        "tmpfs:",
        "/var/lib/postgresql/data",
        "experience-test-only:",
        "internal: true",
    )

    for item in required:
        assert item in source

    # Не разрешаем связывание тестовой БД с инфраструктурой PDRD.
    assert "ai-shared" not in source
    assert "external: true" not in source

    # Тестовая база не должна публиковать TCP-порт.
    assert "\n    ports:" not in source

    # Нельзя подключать реальные project volumes.
    assert "\n    volumes:" not in source


def test_runner_uses_explicit_database_and_selected_tests_only() -> None:
    """Миграция и pytest должны использовать один тестовый PostgreSQL."""
    source = TEST_COMPOSE.read_text(
        encoding="utf-8",
    )

    assert "EXPERIENCE_SERVICE_DATABASE_URL: &test_url" in source
    assert "EXPERIENCE_SERVICE_TEST_DATABASE_URL: *test_url" in source

    assert "PDRD_RUN_DATABASE_TESTS:" in source

    assert "python -m alembic -c alembic.ini upgrade head" in source

    assert (
        "python -m pytest -q "
        "services/experience-service/tests/integration/"
        "test_review_database.py" in source
    )

    # Под флагом DB-тестов запускаем только Experience integration.
    # Общий pytest и тесты других сервисов требуют своей среды.
    assert "python -m pytest -q ." not in source
