# tests/functional/test_requirement_plan_workflows.py

"""Публикуемые n8n-выражения сохраняют раздел и план при пустом списке замечаний."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = """
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const plan = {checks: [{requirement_id: 'req-1', result: 'satisfied', source_ids: ['N1']}],
              coverage: {completed: 1, new_vlm_calls: 0}};
const prepared = {cad: {}, expanded: {document: {}, page: {page_number: 1, text: 'source'}},
                  source: {selected_page: 1}, page: {page_number: 1, text: 'source'}, facts: {},
                  queries: ['base', 'additional'], protected_query_count: 1};
const context = {$itemIndex: 0, $json: {finalization: {findings: [], metrics: {}}, findings: []},
  $: name => {
    const json = name.startsWith('POST ')
      ? {body: {normative_section_id: 'section-1', technical_assignment_id: input.withTA ? 'ta-1' : null}}
      : ['Check Norms', 'Gate Expand Norm Check Stage'].includes(name)
      ? {findings: [], metrics: {requirement_plan: plan}} : prepared;
    return {all: () => [{json}], first: () => ({json}), item: {json}};
  }};
const query = JSON.parse(vm.runInNewContext(input.body.slice(3, -2), context, {timeout: 1000}));
const result = vm.runInNewContext(`(function(){${input.code}})()`, context, {timeout: 1000});
const body = result.json ?? result[0].json;
const saved = body.normative_check ?? body.page?.normative_check;
context.$json = prepared;
const search = JSON.parse(vm.runInNewContext(input.search.slice(3, -2), context, {timeout: 1000}));
const user = JSON.parse(vm.runInNewContext(input.user.slice(3, -2), context, {timeout: 1000}));
process.stdout.write(JSON.stringify({query, saved, search, user, findings: body.findings ?? body.page?.findings}));
"""


@pytest.mark.parametrize(
    "mode,name",
    [
        ("pdf", "Build Page Result"),
        ("cad", "Build CAD Result"),
        ("pdf-cad", "Build PDF CAD Result"),
    ],
)
@pytest.mark.parametrize("with_ta", [False, True])
def test_zero_findings_do_not_erase_requirement_coverage(mode, name, with_ta):
    """Выполняет фактический JS workflow без замены выражений тестовой реализацией."""
    workflow = json.loads(
        (ROOT / f"n8n/workflows/analysis-v2-{mode}.json").read_text("utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}
    result = subprocess.run(
        ["node", "-e", RUNNER],
        input=json.dumps(
            {
                "body": nodes["Build Normative Queries"]["parameters"]["body"],
                "code": nodes[name]["parameters"]["jsCode"],
                "search": nodes["Search Requirements"]["parameters"]["body"],
                "user": nodes["Search User Packages"]["parameters"]["body"],
                "withTA": with_ta,
            }
        ),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=10,
    )
    body = json.loads(result.stdout)
    assert body["query"]["section_id"] == "section-1"
    assert body["saved"]["metrics"]["requirement_plan"]["checks"][0]["source_ids"] == [
        "N1"
    ]
    assert body["saved"]["metrics"]["requirement_plan"]["coverage"]["completed"] == 1
    assert body["findings"] == []
    assert body["search"]["protected_query_count"] == 1
    assert body["search"]["queries"] == ["base", "additional"]
    assert body["user"]["queries"] == ["base"]
