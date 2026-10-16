// frontend/tests/workflow-physical-evidence.test.js

/** Выполняет настоящие HTTP-выражения: компактные метаданные не должны терять исходную геометрию. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const workflow = JSON.parse(readFileSync(new URL("../../n8n/workflows/analysis-v2-pdf.json", import.meta.url), "utf8"));
for (const name of ["Build Page Document Context", "Check Cross-Page Consistency"]) {
  test(`${name}: слова и пути берутся из исходной страницы по номеру`, () => {
    const source = { pages: [25, 24].map((page_number) => ({ page_number,
      text_words: [{ text: `S1-${page_number}`, bbox: { x_min: 1, y_min: 2, x_max: 3, y_max: 4 } }],
      graphic_lines: [{ id: `path-${page_number}`, x1: 10, y1: 20, x2: 30, y2: 40 }],
    })) };
    const compact = [24, 25].map((page_number) => ({ json: { page_number,
      expanded: { page: { page_number, text: "Текст без тяжёлой геометрии" } }, facts: {} } }));
    const input = { pages: compact.map(({ json }) => ({ page_number: json.page_number, page_facts: {} })) };
    const body = workflow.nodes.find((node) => node.name === name).parameters.body;
    const result = JSON.parse(vm.runInNewContext(body.slice(3, -2), {
      $json: input,
      $: (node) => ({ first: () => ({ json: node === "Document Extract PDF" ? source :
        node === "Check Norms" ? { items: [] } : { body: { use_document_context: true } } }), all: () => compact }),
    }, { timeout: 1000 }));
    assert.deepEqual(result.pages.map((page) => page.page_number), [24, 25]);
    for (const page of result.pages) {
      assert.equal(page.text_words[0].text, `S1-${page.page_number}`);
      assert.equal(page.graphic_lines[0].id, `path-${page.page_number}`);
    }
  });
}
