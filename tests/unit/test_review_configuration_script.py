# tests/unit/test_review_configuration_script.py

"""Регрессии приватной настройки Review: идемпотентность, сохранность и отказ prod."""

import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/configure-review.py"
MODULE = runpy.run_path(str(SCRIPT))


def test_closed_review_configuration_preserves_existing_settings_and_keys(tmp_path):
    """Повторный запуск не меняет секреты, настройки БД и другие строки .env."""
    target = tmp_path / ".env"
    preserved = 'PDRD_POSTGRES_PASSWORD="private # kept"\n# Local deployment\nAPI_GATEWAY_ENVIRONMENT=local\n'
    target.write_text(preserved, encoding="utf-8")
    MODULE["configure"](tmp_path, "engineer:test")
    first = target.read_text(encoding="utf-8")
    MODULE["configure"](tmp_path)
    assert target.read_text(encoding="utf-8") == first
    assert first.startswith(preserved)
    settings = MODULE["values"](first)
    assert (
        settings["API_GATEWAY_REVIEW__UI_KEY"]
        != settings["API_GATEWAY_REVIEW__INTERNAL_KEY"]
    )
    assert settings["API_GATEWAY_REVIEW__ENABLED"] == "true"
    assert settings["API_GATEWAY_REVIEW__ACTOR"] == "engineer:test"


@pytest.mark.parametrize(
    "extra",
    [
        "API_GATEWAY_ENVIRONMENT=prod\n",
        "API_GATEWAY_REVIEW__UI_KEY=bad\n",
        "API_GATEWAY_REVIEW__ACTOR=invalid actor\n",
    ],
)
def test_invalid_configuration_leaves_private_file_unchanged(tmp_path, extra):
    """Ошибочная настройка не повреждает существующее приватное окружение."""
    target = tmp_path / ".env"
    target.write_text(extra, encoding="utf-8")
    with pytest.raises(ValueError):
        MODULE["configure"](tmp_path)
    assert target.read_text(encoding="utf-8") == extra
