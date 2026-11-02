# tests/architecture/test_shared_equipment_search.py

"""Проект потребляет shared SearXNG, сохраняя собственное состояние Equipment."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_search_runtime_belongs_to_shared_and_application_state_stays_private():
    """Нет копии SearXNG, shared-секрета или доступа мигратора к внешней сети."""
    stack = yaml.safe_load((ROOT / "compose.yaml").read_text("utf8"))
    assert "searxng" not in stack["services"]
    service = stack["services"]["equipment-search-service"]
    assert "ai-shared" in service["networks"] and "app-net" in service["networks"]
    assert "searxng" not in service["depends_on"]
    assert "ai-shared" not in stack["services"]["equipment-search-migrate"]["networks"]
    assert "SEARXNG_SECRET" not in (ROOT / "compose.yaml").read_text("utf8")
    settings = (ROOT / ".env.example").read_text("utf8")
    assert "EQUIPMENT_SEARCH_SERVICE_SEARXNG_URL=http://searxng:8080" in settings
    assert "PDRD_SEARXNG_IMAGE=" not in settings
