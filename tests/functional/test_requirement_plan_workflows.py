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


@pytest.mark.parametrize("mode", ["pdf", "cad", "pdf-cad"])
@pytest.mark.parametrize("encoded", [False, True])
def test_normative_corpus_reaches_actual_check_norms_expression(mode, encoded):
    """Исполняет публикуемое выражение с настоящей формой multipart-полей Gateway."""
    workflow = json.loads(
        (ROOT / f"n8n/workflows/analysis-v2-{mode}.json").read_text("utf-8")
    )
    expression = next(
        node for node in workflow["nodes"] if node["name"] == "Check Norms"
    )["parameters"]["body"]
    script = """
    const fs = require('node:fs'), vm = require('node:vm');
    const input = JSON.parse(fs.readFileSync(0, 'utf8'));
    const ids = ['norm-1', 'norm-2'], generations = {'norm-1': 'gen-1'};
    const body = {document_id: 'job-1', normative_section_id: 'section-1',
      normative_document_ids: input.encoded ? JSON.stringify(ids) : ids,
      normative_generation_ids: input.encoded ? JSON.stringify(generations) : generations};
    const prepared = {page: {page_number: 1, text: 'source', image_base64: ''},
      analysis_text: 'source', facts: {}, pages: []};
    const context = {$json: {document_id: 'job-1', items: [{page_number: 1, page_facts: {}}]},
      $: name => { const json = name.startsWith('POST ') ? {body} : prepared;
        return {first: () => ({json}), item: {json}}; }};
    process.stdout.write(vm.runInNewContext(input.expression.slice(3, -2), context, {timeout: 1000}));
    """
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"expression": expression, "encoded": encoded}),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=10,
    )
    payload = json.loads(result.stdout)
    assert payload["document_id"] == "job-1"
    assert payload["normative_corpus"] == {
        "section_id": "section-1",
        "document_ids": ["norm-1", "norm-2"],
        "generation_ids": {"norm-1": "gen-1"},
    }


@pytest.mark.parametrize("mode", ["cad", "pdf-cad"])
def test_single_sheet_context_keeps_pz_separate_from_native_text(mode):
    """Те же фрагменты ПЗ доступны CAD и combined без присвоения им страницы чертежа."""
    workflow = json.loads(
        (ROOT / f"n8n/workflows/analysis-v2-{mode}.json").read_text("utf-8")
    )
    expression = next(
        node for node in workflow["nodes"] if node["name"] == "Check Norms"
    )["parameters"]["body"]
    script = """
    const fs = require('node:fs'), vm = require('node:vm');
    const expression = JSON.parse(fs.readFileSync(0, 'utf8'));
    const original = {page: {page_number: 1, text: 'NATIVE', image_base64: 'eA=='}, facts: {}};
    const pz = {analysis_text: 'NATIVE MIXED PZ', project_context_texts: ['FULL PZ QUOTE'],
      sources: [{source_id: 'PZ1', page: 16, score: 0.8, text_excerpt: 'SHORT'}]};
    const context = {$json: {sources: []}, $: name => {
      const json = name.startsWith('POST ') ? {body: {document_id: 'job'}}
        : name === 'Augment Project Context' ? pz : original;
      return {item: {json}, first: () => ({json})};
    }};
    process.stdout.write(vm.runInNewContext(expression.slice(3, -2), context));
    """
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(expression),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=10,
    )
    payload = json.loads(result.stdout)
    assert payload["extracted_text"] == "NATIVE"
    assert payload["project_context_sources"][0]["page"] == 16
    assert payload["project_context_sources"][0]["text"] == "FULL PZ QUOTE"
