# tests/architecture/test_understanding_no_recovery.py

"""Бюджетное продолжение не может зависеть от VLM или повторять визуальное распознавание."""

import ast
from pathlib import Path

APPLICATION = (
    Path(__file__).resolve().parents[2]
    / "services/analysis-service/src/pdrd_analysis_service/application"
)


def test_overflow_continuation_has_no_external_dependency_or_model_request():
    """Модуль продолжения остаётся чистым преобразованием без доступа к портам и инфраструктуре."""
    tree = ast.parse(
        (APPLICATION / "understanding_recovery.py").read_text(encoding="utf-8")
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom, ast.Await))
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "generate_json"
        for node in ast.walk(tree)
    )


def test_main_understanding_contains_only_one_model_request_site():
    """Новый цикл или обращение к прежнему recovery не добавляется в основной сценарий."""
    source = (APPLICATION / "use_cases/understanding.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "generate_json"
    ]
    assert len(calls) == 1
    assert "recover_page_understanding" not in source
