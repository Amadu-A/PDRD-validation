// frontend/tests/document-context.test.js

/** Один D finding, отдельные страницы и выбор режима анализа. */
import assert from "node:assert/strict";
import test from "node:test";
import { findingSourceKinds, findingPageLabel, focusDocumentEvidence } from "../src/js/features/analysis/finding-provenance.js";
import { renderAnalysisReport } from "../src/js/features/analysis/report.js";
import { createAutomaticReview } from "../src/js/features/review/automatic.js";
import { registerVisualizationReview } from "../src/js/features/analysis/visualization-review.js";
import { FakeElement } from "./helpers/fake-dom.js";

function setup(t) {
  const previous = { document: globalThis.document, window: globalThis.window };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag), createDocumentFragment: () => new FakeElement("fragment"),
    createTextNode: (text) => Object.assign(new FakeElement("text"), { textContent: text }) };
  globalThis.window = { setTimeout: (callback) => { callback(); } };
}
const finding = { finding_id: "cross-page-0001", page: 7, status: "confirmed", category: "document_consistency", comment: "Б-012: -37 / -35",
  document_context_basis_sources: [{ source_id: "D-p0007-f0001", page: 7, evidence_text: "-37" }, { source_id: "D-p0010-f0001", page: 10, evidence_text: "-35" }],
  evidence_locations: [{ page: 7, text: "-37" }, { page: 10, text: "-35" }] };

test("D и N определяются массивами; типы модели и категория не создают бейдж", () => {
  assert.deepEqual(findingSourceKinds(finding), ["D"]);
  assert.deepEqual(findingSourceKinds({ ...finding, basis_sources: [{}] }), ["D", "N"]);
  assert.deepEqual(findingSourceKinds({ category: "document_consistency", source_kinds: ["D"] }), []);
  assert.equal(findingPageLabel(finding), "Страницы 7, 10");
});

test("отчёт содержит одно замечание, D ссылки на обе страницы и общий заголовок", (t) => {
  setup(t);
  const report = renderAnalysisReport({ status: "completed", source_mode: "pdf_only", selected_pages: [7, 10], findings: [finding] });
  const rows = report.querySelectorAll(".analysis-result__finding");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].dataset.findingId, finding.finding_id);
  assert.equal(rows[0].querySelector(".analysis-result__finding-title").textContent, "№1 · Страницы 7, 10");
  assert.deepEqual(rows[0].querySelectorAll(".analysis-result__source-kind").map((node) => node.textContent), ["[D]"]);
  const sources = rows[0].querySelectorAll(".analysis-result__sources").flatMap((node) => node.children).filter((node) => node.tagName === "A");
  assert.deepEqual(sources.map((node) => node.href), ["#analysis-page-7", "#analysis-page-10"]);
});

test("переход D подсвечивает область своего ID и не вызывает поиск Knowledge", (t) => {
  setup(t);
  const page = new FakeElement();
  page.scrollIntoView = () => { page.scrolled = true; };
  const box = new FakeElement(); box.className = "analysis-result__bbox"; box.dataset.findingId = finding.finding_id;
  page.append(box);
  globalThis.document.getElementById = (id) => id === "analysis-page-10" ? page : null;
  let clear;
  globalThis.window.setTimeout = (callback) => { clear = callback; };
  const link = new FakeElement("a");
  focusDocumentEvidence(link, finding.finding_id, 10);
  link.click();
  assert.equal(page.scrolled, true);
  assert.equal(box.classList.contains("is-document-evidence-focus"), true);
  clear();
  assert.equal(box.classList.contains("is-document-evidence-focus"), false);
});

test("Review восстанавливает отдельную геометрию страниц и отклоняет обе одним ID", (t) => {
  setup(t);
  const root = new FakeElement();
  const records = [];
  for (const pageNumber of [7, 10]) {
    const page = new FakeElement(); page.className = "analysis-result__page-visualization";
    const pane = new FakeElement(); const callout = new FakeElement(); const item = new FakeElement(); const bbox = new FakeElement();
    const record = { findingId: finding.finding_id, pageNumber, secondaryEvidence: pageNumber !== 7, item, callout,
      bboxEntries: [{ node: bbox, box: { xMin: 100, yMin: 100, xMax: 200, yMax: 200 } }], connectorEntries: [] };
    records.push(record);
    registerVisualizationReview(page, { records: [record], imagePane: pane, redraw() {} });
    root.append(page);
  }
  const automatic = createAutomaticReview({ onGeometry() {}, onRecord() {}, onStart() {} });
  automatic.mount(root);
  const first = { x_min: 120, y_min: 120, x_max: 220, y_max: 220 };
  const second = { x_min: 400, y_min: 500, x_max: 500, y_max: 530 };
  automatic.restore([{ finding_id: finding.finding_id, display_regions: [first], proposed_regions: [{ bbox: first }],
    evidence_locations: [{ page: 10, proposed_regions: [{ bbox: second }] }] }]);
  assert.equal(records[0].bboxEntries[0].box.xMin, 120);
  assert.equal(records[1].bboxEntries[0].box.xMin, 400);
  assert.equal(automatic.snapshot(finding.finding_id).length, 1);
  automatic.setRejected(finding.finding_id, true);
  assert.equal(records.every((record) => record.bboxEntries[0].node.classList.contains("is-hidden")), true);
  automatic.setRejected(finding.finding_id, false);
  assert.equal(records.every((record) => !record.bboxEntries[0].node.classList.contains("is-hidden")), true);
  automatic.dispose();
});


test("Review сохраняет все D-рамки страницы, ограничивая операционную правку четырьмя основными", (t) => {
  setup(t);
  const root = new FakeElement();
  const records = [];
  const boxes = Array.from({ length: 6 }, (_, i) => ({ x_min: 100 + i * 80, y_min: 100, x_max: 150 + i * 80, y_max: 150 }));
  for (const pageNumber of [7, 10]) {
    const page = new FakeElement(); page.className = "analysis-result__page-visualization";
    const record = { findingId: finding.finding_id, pageNumber, secondaryEvidence: pageNumber === 10, documentEvidence: true,
      item: new FakeElement(), callout: new FakeElement(), connectorEntries: [],
      bboxEntries: boxes.map((box) => ({ node: new FakeElement(), box: { xMin: box.x_min, yMin: box.y_min, xMax: box.x_max, yMax: box.y_max } })) };
    records.push(record);
    registerVisualizationReview(page, { records: [record], imagePane: new FakeElement(), redraw() {} });
    root.append(page);
  }
  const automatic = createAutomaticReview({ onGeometry() {}, onRecord() {}, onStart() {} });
  automatic.mount(root);
  automatic.restore([{ finding_id: finding.finding_id, proposed_regions: boxes.slice(0, 4).map((bbox) => ({ bbox })),
    evidence_locations: boxes.map((bbox) => ({ page: 10, proposed_regions: [{ bbox }] })) }]);
  assert.equal(automatic.snapshot(finding.finding_id)[0].proposed_issue_boxes.length, 4);
  for (const record of records) {
    assert.equal(record.bboxEntries[5].box.xMin, boxes[5].x_min);
  }
  automatic.setRejected(finding.finding_id, true);
  assert.equal(records.every((record) => record.bboxEntries.every(({ node }) => node.classList.contains("is-hidden"))), true);
  automatic.dispose();
});
