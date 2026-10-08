// frontend/tests/document-context.test.js

/** Межстраничные доказательства, компактные карточки и общие решения Review. */
import assert from "node:assert/strict";
import test from "node:test";
import { findingSourceKinds, findingSourceDescription, findingPageLabel, focusDocumentEvidence } from "../src/js/features/analysis/finding-provenance.js";
import { renderAnalysisReport } from "../src/js/features/analysis/report.js";
import { createAutomaticReview } from "../src/js/features/review/automatic.js";
import { registerVisualizationReview } from "../src/js/features/analysis/visualization-review.js";
import { FakeElement } from "./helpers/fake-dom.js";

/** Устанавливает минимальный DOM и восстанавливает окружение после каждого сценария. */
function setup(t) {
  const previous = { document: globalThis.document, window: globalThis.window };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag), createElementNS: (_namespace, tag) => new FakeElement(tag), createDocumentFragment: () => new FakeElement("fragment"),
    createTextNode: (text) => Object.assign(new FakeElement("text"), { textContent: text }) };
  globalThis.window = { setTimeout: (callback) => { callback(); }, requestAnimationFrame: () => {}, addEventListener: () => {} };
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
  assert.equal(rows[0].querySelectorAll(".analysis-result__source-kind").length, 0);
  assert.ok(rows[0].querySelectorAll(".analysis-result__field-value").some((node) => node.textContent.includes("D — факты проверяемого PDF")));
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


test("пустой основной массив не скрывает сохранённые N/T/U источники", () => {
  const item = {
    basis_sources: [], normative_sources: [{ source_id: "N1" }],
    technical_assignment_basis_sources: [], technical_assignment_sources: [{ source_id: "T1" }],
    user_package_basis_sources: [], user_package_sources: [{ source_id: "U1" }],
  };
  assert.deepEqual(findingSourceKinds(item), ["N", "T", "U"]);
  assert.doesNotMatch(findingSourceDescription(item), /Инженерное замечание/);
  assert.match(findingSourceDescription(item), /нормативное основание/);
});

test("полный отчёт объясняет инженерное замечание и роль ПЗ без выдуманного типа", (t) => {
  setup(t);
  const row = { finding_id: "p7-f1", page: 7, comment: "Насос не подключён", project_context_sources: [{ source_id: "PZ1", page: 2 }] };
  const report = renderAnalysisReport({ status: "completed", findings: [row] });
  const texts = report.querySelectorAll(".analysis-result__field-value").map((node) => node.textContent).join("\n");
  assert.match(texts, /Инженерное замечание: сохранённые источники N\/T\/U\/D\/E отсутствуют/);
  assert.match(texts, /ПЗ — контекст пояснительной записки/);
  assert.deepEqual(findingSourceKinds(row), []);
});


/** Строит настоящий отчёт с визуализацией, не вызывая backend или GPU. */
function drawingReport(items) {
  return renderAnalysisReport({ status: "completed", source_mode: "pdf_only", findings: items }, {
    visualization: { status: "completed", pages: [7, 10].map((page) => ({ page_number: page, image_base64: "" })) },
  });
}

test("карточка содержит конкретный текст и рабочие ссылки N/T/U/D без классификации", (t) => {
  setup(t);
  const item = { ...finding, comment: "Для помещения Б-012 указаны разные температуры: -37 и -35 °C.",
    basis_sources: [], normative_sources: [{ document_id: "norm-1", source_file: "СП 60.pdf", page: 4 }],
    technical_assignment_basis_sources: [], technical_assignment_sources: [{ technical_assignment_id: "tz-1", page: 2 }],
    user_package_basis_sources: [], user_package_sources: [{ document_id: "user-1", source_file: "Требования.pdf", page: 3 }],
    project_context_sources: [{ source_id: "PZ1", page: 2 }],
  };
  const report = drawingReport([item]);
  const cards = report.querySelectorAll(".analysis-result__annotation");
  assert.equal(cards.length, 2);
  for (const card of cards) {
    assert.equal(card.querySelector(".analysis-result__annotation-title").textContent, item.comment);
    assert.equal(card.querySelectorAll(".analysis-result__source-kind").length, 0);
    const links = card.querySelectorAll(".analysis-result__source-link");
    assert.deepEqual(links.map((node) => node.href), [
      "/api/v1/normative/documents/norm-1/content#page=4", "#",
      "/api/v1/normative/user-packages/documents/user-1/content#page=3", "#analysis-page-7", "#analysis-page-10",
    ]);
    assert.deepEqual(links.map((node) => node.textContent), ["СП 60 · стр. 4", "ТЗ · стр. 2", "Требования · стр. 3", "PDF · стр. 7", "PDF · стр. 10"]);
    assert.ok(links[1].listeners.get("click").length, "ТЗ использует защищённый обработчик открытия");
    assert.ok(links.every((node) => node.getAttribute("title") === undefined));
    const info = card.querySelector(".analysis-result__annotation-info-button");
    assert.equal(info.getAttribute("aria-label"), "Полное замечание №1");
    assert.ok(info.getAttribute("aria-describedby"));
    assert.equal(card.querySelector("[data-review-tooltip-text]").textContent, item.comment);
  }
});

test("без маршрута или корректной страницы источник не создаёт ссылку на чертеже", (t) => {
  setup(t);
  const report = drawingReport([{ finding_id: "p7-f1", page: 7, comment: "Насос не подключён", basis_sources: [{ source_file: "СП.pdf", page: 2 }],
    technical_assignment_basis_sources: [{ technical_assignment_id: "tz", page: -1 }],
    user_package_basis_sources: [{ document_id: "user", page: "неизвестно" }],
    project_context_sources: [{ source_id: "PZ1", page: 2 }],
  }]);
  const card = report.querySelector(".analysis-result__annotation");
  assert.equal(card.querySelectorAll(".analysis-result__source-link").length, 0);
  assert.equal(card.querySelectorAll(".analysis-result__annotation-sources").length, 0);
  assert.equal(card.querySelectorAll(".analysis-result__source-kind").length, 0);
});

test("общая карточка сохраняет самостоятельный текст и ссылки каждого замечания", (t) => {
  setup(t);
  const location = { status: "located", bbox: { x_min: 100, y_min: 100, x_max: 200, y_max: 200 } };
  const items = [
    { finding_id: "p7-f1", page: 7, object_ref: "КЛ-12", comment: "Не указан сценарий нагрузки", location,
      normative_sources: [{ document_id: "n1", source_file: "СП 60.pdf", page: 4 }] },
    { finding_id: "p7-f2", page: 7, object_ref: "КЛ-12", comment: "Тип кабеля противоречит ТЗ", location,
      technical_assignment_sources: [{ technical_assignment_id: "t1", page: 2 }] },
  ];
  const report = drawingReport(items);
  const rows = report.querySelectorAll(".analysis-result__group-member");
  assert.equal(rows.length, 2);
  for (const [index, row] of rows.entries()) {
    assert.equal(row.dataset.findingId, items[index].finding_id);
    assert.equal(row.querySelector(".analysis-result__group-member-text").textContent, items[index].comment);
    assert.equal(row.querySelectorAll(".analysis-result__source-link").length, 1);
    assert.equal(row.querySelectorAll(".analysis-result__source-kind").length, 0);
  }
});
