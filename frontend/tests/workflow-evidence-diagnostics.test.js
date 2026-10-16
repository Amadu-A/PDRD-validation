// frontend/tests/workflow-evidence-diagnostics.test.js

/** Проверяет сохранение диагностики в реальном коде сборки результата, отдельно от замечаний. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

for (const [mode, name] of [["pdf", "Build Page Result"], ["cad", "Build CAD Result"], ["pdf-cad", "Build PDF CAD Result"]]) {
  const workflow = JSON.parse(readFileSync(new URL(`../../n8n/workflows/analysis-v2-${mode}.json`, import.meta.url), "utf8"));
  const code = workflow.nodes.find((node) => node.name === name).parameters.jsCode;
  test(`${mode}: полное происхождение диагностики сохраняется вне пользовательских findings`, () => {
    const diagnostic = { finding_id: "internal-1", reason_code: "different_parameter_definitions", candidate: {
      origin_assertions: [{ origin: "vlm", candidate_id: "raw-1", comment: "Исходный текст" }],
      evidence_locations: [{ page: 1 }, { page: 2 }], document_context_source_ids: ["D-p0001-f0001", "D-p0002-f0001"],
    } };
    const finalization = { summary: "Готово", findings: [{ finding_id: "question-1", status: "needs_review" }],
      metrics: { rejected_findings: [diagnostic], vlm_call_count: 0 } };
    const prepared = { cad: {}, expanded: { document: {}, page: { page_number: 1 } }, source: { selected_page: 1 }, page: { page_number: 1 } };
    const result = vm.runInNewContext(`(function() { ${code} })()`, {
      $json: mode === "pdf" ? { finalization } : finalization, $itemIndex: 0,
      $: () => ({ all: () => [{ json: prepared }], first: () => ({ json: prepared }), item: { json: prepared } }),
    }, { timeout: 1000 });
    const body = result.json ?? result[0].json;
    const saved = body.finalization ?? body.page?.finalization;
    assert.deepEqual(JSON.parse(JSON.stringify(saved.metrics.rejected_findings)), [diagnostic]);
    assert.deepEqual(Array.from(body.findings, (finding) => finding.finding_id), ["question-1"]);
    if (mode === "pdf") {
      const aggregate = workflow.nodes.find((node) => node.name === "Aggregate PDF Result").parameters.jsCode;
      const output = vm.runInNewContext(`(function() { ${aggregate} })()`, {
        $input: { all: () => [{ json: body }] }, $: () => ({ first: () => ({ json: { body: {} } }) }),
      }, { timeout: 1000 })[0].json;
      assert.equal(output.pages[0].finalization.metrics.rejected_findings[0].candidate.origin_assertions[0].candidate_id, "raw-1");
      assert.equal(output.findings.length, 1);
    }
  });
}
