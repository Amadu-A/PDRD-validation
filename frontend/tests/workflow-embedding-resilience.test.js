// frontend/tests/workflow-embedding-resilience.test.js

/** Исполняет опубликованные Code-узлы: метрики сохраняются, неполный поиск не становится успехом. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

for (const mode of ["pdf", "cad", "pdf-cad"]) {
  const workflow = JSON.parse(fs.readFileSync(new URL(`../../n8n/workflows/analysis-v2-${mode}.json`, import.meta.url), "utf8"));
  const node = workflow.nodes.find((entry) => entry.name === ({pdf: "Aggregate PDF Result", cad: "Build CAD Result", "pdf-cad": "Build PDF CAD Result"})[mode]);
  const metric = {operation_id: "op-1", physical_requests: 3, http_503: 2, job_id: "job", stage: "requirements"};
  const prepared = {cad: {original_file_name: "drawing.dxf"}, source: {selected_page: 1}, page: {page_number: 1}, facts: {},
    queries: ["base", "additional"], protected_query_count: 1};
  function execute(retrieval) {
    const context = {$json: {findings: []}, $input: {all: () => [{json: {page_number: 1, findings: []}}]},
      $: (name) => {
        const json = name === "Search Requirements" ? retrieval : prepared;
        return {all: () => [{json}], first: () => ({json}), item: {json}};
      }};
    return vm.runInNewContext(`(function(){${node.parameters.jsCode}})()`, context, {timeout: 1000});
  }
  test(`${mode}: метрики embedding проходят до результата без потери source IDs`, () => {
    const normalize = workflow.nodes.find((entry) => entry.name === "Normalize Requirement Search");
    const normalized = vm.runInNewContext(`(function(){${normalize.parameters.jsCode}})()`, {
      $json: {sources: [{source_id: "N7", page: 11}], retrieval_metrics: metric, retrieval_complete: true},
    }, {timeout: 1000});
    assert.equal(normalized.json.normative_sources[0].source_id, "N7");
    assert.equal(normalized.json.normative_sources[0].page, 11);
    assert.equal(normalized.json.retrieval_metrics.http_503, 2);
    const value = execute({retrieval_metrics: metric, retrieval_complete: true});
    const result = value.json ?? value[0].json;
    assert.equal(result.status, "completed");
    assert.equal(result.retrieval_operations[0].retrieval_metrics.physical_requests, 3);
  });
  test(`${mode}: незавершённый поиск не становится completed, отмена обозначена отдельно`, () => {
    assert.throws(() => execute({retrieval_metrics: metric, retrieval_complete: false}), /поиск источников не завершён/);
    const value = execute({retrieval_metrics: metric, retrieval_complete: false, cancelled: true});
    assert.equal((value.json ?? value[0].json).status, "cancelled");
  });
  test(`${mode}: отмена любой страницы останавливает весь batch до строгого обработчика источников`, () => {
    const guards = workflow.nodes.filter((entry) => entry.name.endsWith(" Retrieval Response"));
    assert.ok(guards.length >= 4);
    for (const guard of guards) {
      const condition = guard.parameters.conditions.conditions[0];
      for (const cancelled of [true, false]) {
        const items = [{json: {source_id: "N1"}}, {json: {cancelled, source_id: "N2"}}];
        for (const item of items) {
          const value = vm.runInNewContext(condition.leftValue.slice(3, -2), {
            $json: item.json, $input: {all: () => items},
          }, {timeout: 1000});
          const branch = value !== condition.rightValue ? 0 : 1;
          const targets = workflow.connections[guard.name].main[branch].map((edge) => edge.node);
          if (cancelled) assert.deepEqual(targets, ["Build Cancelled Result"]);
          else assert.ok(!targets.includes("Build Cancelled Result"));
        }
        assert.deepEqual(items.map((item) => item.json.source_id), ["N1", "N2"]);
      }
    }
  });
}
