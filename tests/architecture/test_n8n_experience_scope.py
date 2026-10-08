# tests/architecture/test_n8n_experience_scope.py

"""Контракт нормативного раздела при вызове рабочего E во всех ветках анализа."""

import json
import subprocess
from pathlib import Path

import pytest

NODE_RUNNER = """
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {
  $json: input.current,
  $itemIndex: 0,
  $: () => ({ item: { json: input.parent }, all: () => [{ json: input.parent }] }),
};
const result = vm.runInNewContext(`(() => {\n${input.code}\n})()`, context, {
  timeout: 1000,
});
process.stdout.write(JSON.stringify(result));
"""


def _run_code(code: str, *, current: dict, parent: dict) -> dict:
    """Выполняет настоящий код узла n8n на фиксированном входе без сервера n8n."""
    result = subprocess.run(
        ["node", "-e", NODE_RUNNER],
        input=json.dumps(
            {"code": code, "current": current, "parent": parent},
            ensure_ascii=False,
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Node sandbox завершился с кодом {result.returncode}: {result.stderr}"
        )
    return json.loads(result.stdout)


def _workflow_nodes(kind: str) -> dict:
    """Загружает фактически публикуемый workflow указанной ветки анализа."""
    root = Path(__file__).resolve().parents[2]
    workflow = json.loads(
        (root / f"n8n/workflows/analysis-v2-{kind}.json").read_text(encoding="utf-8")
    )
    return {node["name"]: node for node in workflow["nodes"]}


@pytest.mark.parametrize("kind", ["pdf", "cad", "pdf-cad"])
def test_workflow_experience_uses_server_analysis_normative_scope(kind):
    """Без раздела новая маршрутизация E намеренно не выполняет поиск по другим базам."""
    nodes = _workflow_nodes(kind)
    trigger = f"POST /analysis/v2/{kind}"
    assert trigger in nodes
    body = nodes["Search Experience"]["parameters"]["body"]
    assert "queries: $json.experience_queries" in body
    assert (
        f"section_id: $('{trigger}').first().json.body?.normative_section_id ?? null"
        in body
    )


@pytest.mark.parametrize("kind", ["pdf", "cad", "pdf-cad"])
@pytest.mark.parametrize("empty_at", [0, 1])
def test_workflow_experience_results_stay_with_their_findings(kind, empty_at):
    """Пустой запрос первого или среднего замечания не сдвигает ответ E."""
    nodes = _workflow_nodes(kind)
    findings = [
        {
            "finding_id": f"finding-{index}",
            "experience_query": "" if index == empty_at else f"query-{index}",
        }
        for index in range(3)
    ]
    prepared = _run_code(
        nodes["Prepare Experience Queries"]["parameters"]["jsCode"],
        current={"results": []},
        parent={
            "page_index": 0,
            "page_number": 7,
            "findings": findings,
            "normative_query_items": [],
        },
    )["json"]
    assert prepared["experience_queries"] == [
        f"query-{index}" for index in range(3) if index != empty_at
    ]
    assert [item["finding_id"] for item in prepared["experience_query_items"]] == [
        f"finding-{index}" for index in range(3) if index != empty_at
    ]

    results = [
        {"query": item["query"], "sources": [{"source_id": item["finding_id"]}]}
        for item in prepared["experience_query_items"]
    ]
    mapped = _run_code(
        nodes["Build Experience Map"]["parameters"]["jsCode"],
        current={"results": results},
        parent=prepared,
    )["json"]
    assert mapped["findings"] == findings
    assert mapped["experience_by_finding"] == {
        f"finding-{index}": (
            [] if index == empty_at else [{"source_id": f"finding-{index}"}]
        )
        for index in range(3)
    }
