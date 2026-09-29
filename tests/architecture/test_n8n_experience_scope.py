# tests/architecture/test_n8n_experience_scope.py

"""Контракт нормативного раздела при вызове рабочего E во всех ветках анализа."""

import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("kind", ["pdf", "cad", "pdf-cad"])
def test_workflow_experience_uses_server_analysis_normative_scope(kind):
    """Без раздела новая маршрутизация E намеренно не выполняет поиск по другим базам."""
    root = Path(__file__).resolve().parents[2]
    workflow = json.loads(
        (root / f"n8n/workflows/analysis-v2-{kind}.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}
    trigger = f"POST /analysis/v2/{kind}"
    assert trigger in nodes
    body = nodes["Search Experience"]["parameters"]["body"]
    assert "queries: $json.experience_queries" in body
    assert (
        f"section_id: $('{trigger}').first().json.body?.normative_section_id ?? null"
        in body
    )
