// frontend/tests/workflow-large-t-feed.test.js

/** Исполняет настоящий код n8n: полный feed, конечный предел и отсутствие усечения. */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

for (const mode of ["pdf", "cad", "pdf-cad"]) {
  const workflow = JSON.parse(readFileSync(new URL(`../../n8n/workflows/analysis-v2-${mode}.json`, import.meta.url), "utf8"));
  const code = workflow.nodes.find((node) => node.name === "Technical Assignment Requirement Feed").parameters.jsCode;
  const loader = workflow.nodes.find((node) => node.name === "Load Technical Assignment Requirements");
  const pageLimit = loader.parameters.options.pagination.pagination.maxRequests;
  assert.equal(pageLimit * 200, 2000);
  function run(total, duplicate = false) {
    /** Пагинация Knowledge сохраняет принадлежность документа и порядок требований. */
    const requirements = Array.from({ length: total }, (_, i) => ({ requirement_id: `R-${duplicate ? 1 : i + 1}`, requirement_index: i + 1 }));
    const metadata = { technical_assignment_id: "T", analysis_document_id: "D", section_id: "S", total,
      source_file: "ТЗ.pdf", source_sha256: "a".repeat(64) };
    const pages = [];
    for (let start = 0; start < total; start += 200) pages.push({ json: { ...metadata, requirements: requirements.slice(start, start + 200) } });
    return vm.runInNewContext(`(function() { ${code} })()`, {
      $input: { all: () => pages },
      $: () => ({ first: () => ({ json: { body: { technical_assignment_id: "T", technical_assignment_analysis_document_id: "D", normative_section_id: "S" } } }) }),
    }, { timeout: 1000 });
  }
  test(`${mode}: все 1094 требования остаются в feed`, () => {
    const result = run(1094)[0].json;
    assert.equal(result.total, 1094); assert.equal(result.requirements.length, 1094);
    assert.equal(result.requirements[1093].requirement_id, "R-1094");
  });
  test(`${mode}: предел 2000, неполнота и дубликаты не маскируются`, () => {
    assert.equal(run(2000)[0].json.requirements.length, 2000);
    assert.throws(() => run(2001), /limit=2000/);
    assert.throws(() => run(1094, true), /повторяющиеся/);
  });
}
