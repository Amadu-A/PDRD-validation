// frontend/tests/workflow-pdf-facts.test.js

/** Проверяет реальные выражения workflow: D получает те же строки, что и EQ. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const workflow = JSON.parse(fs.readFileSync(new URL("../../n8n/workflows/analysis-v2-pdf.json", import.meta.url), "utf8"));
const nodes = new Map(workflow.nodes.map((node) => [node.name, node]));
const field = nodes.get("Document Extract PDF").parameters.bodyParameters.parameters.find((item) => item.name === "include_equipment_specification");

for (const d of [false, true, "false", "true", undefined]) {
  for (const eq of [false, true, "false", "true", undefined]) {
    test(`Извлечение единой спецификации: D=${d}, EQ=${eq}`, () => {
      const result = vm.runInNewContext(field.value.slice(3, -2), { $json: { body: { use_document_context: d, use_equipment_web_search: eq } } });
      assert.equal(result, String(String(d) === "true" || String(eq) === "true"));
    });
  }
}

test("D передаёт все программные факты и происхождение без лимита VLM", () => {
  const facts = Array.from({ length: 1001 }, (_, index) => ({
    identifier: `spec-${index}`, source_item_id: `spec-${index}`, source_row_ids: [`row-${index}`],
    source_pages: [24, 25], source_regions: [{ page: 25, row_id: `row-${index}`, x_min: 10, y_min: 20, x_max: 100, y_max: 40 }],
    extraction_method: "pdf_table", value_raw: String(index),
  }));
  const items = [{ page_number: 25, result: { facts: { summary: "Таблица", document_facts: facts } } }];
  const saved = new Map([
    ["POST /analysis/v2/pdf", { body: { document_id: "job" } }],
    ["Compact PDF Metadata", { source_sha256: "sha" }],
    ["Understand Pages Stage", { items }],
    ["Document Extract PDF", { pages: [{ page_number: 25, text_words: [] }] }],
    ["Gate Collect Understanding Stage", { expanded_items: [{ page: { text: "Спецификация" } }] }],
  ]);
  const context = { $json: { items }, $: (name) => ({ first: () => ({ json: saved.get(name) }) }) };
  const body = vm.runInNewContext(nodes.get("Create Document Context").parameters.body.slice(3, -2), context);
  const output = JSON.parse(body);
  assert.deepEqual(output.pages[0].document_facts, facts);
});
