# tests/architecture/test_quality_script_safety.py

"""Отделяет локальные проверки качества от ручной публикации изменений."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUALITY_SCRIPT = ROOT / "ops" / "check-quality.ps1"


def test_quality_command_cannot_stage_commit_or_push() -> None:
    """Команда проверки не должна менять индекс Git и отправлять код в репозитории."""
    source = QUALITY_SCRIPT.read_text(encoding="utf-8-sig")

    assert "[switch]$Fix" in source
    assert "$CommitMessage" not in source
    assert "$Push" not in source
    assert '("add", "--all")' not in source
    assert '("commit", "-m"' not in source
    assert '("push",' not in source
    assert '("diff", "--check")' in source
