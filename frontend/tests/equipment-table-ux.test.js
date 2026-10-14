// frontend/tests/equipment-table-ux.test.js

/** Пять столбцов, десять реальных DOM-строк, безопасные документы и сохранённые области. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { appendEquipmentReport, equipmentDisplayName, equipmentRows, equipmentRowId, equipmentDocumentStatus } from "../src/js/features/analysis/equipment-report.js";
import { focusPageEvidence, safeExternalSourceUrl } from "../src/js/features/analysis/finding-provenance.js";

function collect(node) { return [node, ...node.children.flatMap(collect)]; }
function fixture() {
  const previous = { document: globalThis.document, raf: globalThis.requestAnimationFrame, cancel: globalThis.cancelAnimationFrame };
  const frames = new Map(); let next = 0;
  globalThis.document = { createElement: (tag) => new FakeElement(tag), getElementById: () => null };
  globalThis.requestAnimationFrame = (callback) => { frames.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  return { flush() { for (const [id, callback] of [...frames]) { frames.delete(id); callback(); } },
    restore() { globalThis.document = previous.document; globalThis.requestAnimationFrame = previous.raf; globalThis.cancelAnimationFrame = previous.cancel; } };
}

test("наименование содержит проектный тип, исполнение и изготовителя; ключ переживает фильтр", () => {
  const row = { identity: { manufacturer: "" }, manufacturer: "Уточнённый по паспорту", model: "РИП-12", variant: "исп. 51",
    occurrences: [{ equipment_type: "Блок питания", page: 1, object_ref: "БП1" }] };
  assert.equal(equipmentDisplayName(row), "Блок питания · РИП-12, исп. 51");
  assert.equal(equipmentRowId(row), equipmentRowId({ ...row, manufacturer: "Другое уточнение" }));
});

test("позиции одной модели не исчезают: сравнение и область относятся к конкретной строке", () => {
  const model = { model: "MODEL-1", manufacturer: "Завод", status: "mismatch", document: { source_id: "EQ-" + "a".repeat(32) },
    occurrences: [{ page: 24, object_ref: "1", source_row_ids: ["r1"] }, { page: 25, object_ref: "2", source_row_ids: ["r2"] }],
    comparisons: [{ page: 24, object_ref: "1", status: "mismatch_candidate" }, { page: 25, object_ref: "2", status: "matched" }] };
  const position = (id, page) => ({ item_id: "p" + id, row_ids: ["r" + id], position: String(id), Caption: "Полная позиция " + id,
    manufacturer: "Завод", model: "MODEL-1", source_pages: [page], quantity: "2", units: "шт." });
  const rows = equipmentRows({ models: [model], specification: { structured_items: [position(1, 24), position(2, 25)] } });
  assert.equal(rows.length, 2); assert.equal(rows[0].status, "mismatch"); assert.equal(rows[1].status, "confirmed");
  assert.deepEqual(rows.map((row) => row.equipment_id), ["p1", "p2"]);
  assert.deepEqual(rows.map((row) => row.pages), [[24], [25]]);
  assert.equal(equipmentDisplayName(rows[1]), "Полная позиция 2");
});

for (const count of [11, 300, 500]) test(`${count} позиций: DOM содержит не больше 10 строк; внутренний скролл достигает конца`, () => {
  const environment = fixture();
  try {
    const models = Array.from({ length: count }, (_, index) => ({ model: `MODEL-${index}`, status: "not_checked", metrics: {} }));
    const component = appendEquipmentReport(new FakeElement(), { models }, { autoUpdates: false });
    environment.flush();
    const rows = () => collect(component.root).filter((node) => node.tagName === "TR" && node.dataset.equipmentId);
    assert.equal(rows().length, 10);
    const scroll = component.root.querySelector(".equipment-report__scroll");
    assert.ok(parseFloat(scroll.style.maxHeight) > 0);
    assert.equal(scroll.dataset.visibleFrom, "1");
    scroll.scrollTop = 1_000_000; scroll.dispatch("scroll"); environment.flush();
    assert.equal(rows().length, 10);
    assert.equal(scroll.dataset.visibleTo, String(count));
    assert.ok(collect(rows().at(-1)).some((node) => node.textContent === `MODEL-${count - 1}`));
    assert.equal(new Set(rows().map((row) => row.dataset.equipmentId)).size, 10);
    component.dispose();
  } finally { environment.restore(); }
});

test("только прочитанный snapshot получает документ; неподтверждённый источник сохраняет свою отметку", () => {
  const environment = fixture();
  try {
    const read = { model: "MODEL-1", status: "confirmed", document: { source_id: "EQ-" + "a".repeat(32),
      final_url: "https://manufacturer.example/manual.pdf", source_url: "https://manufacturer.example/product",
      trust_status: "unverified_user_override", revision: "B" }, metrics: {} };
    const foundOnly = { model: "MODEL-2", status: "incomplete", attempts: [{ url: "https://search.example/found.pdf" }],
      document: { final_url: "https://search.example/found.pdf" }, metrics: {} };
    const component = appendEquipmentReport(new FakeElement(), { models: [read, foundOnly] }, { autoUpdates: false });
    const nodes = collect(component.root);
    const urls = nodes.filter((node) => node.tagName === "A").map((node) => node.href);
    assert.ok(urls.includes(read.document.final_url)); assert.ok(!urls.includes(foundOnly.document.final_url));
    assert.ok(nodes.some((node) => node.textContent.includes("неподтверждённый источник")));
    assert.equal(equipmentDocumentStatus(read), "Соответствует");
    assert.equal(equipmentDocumentStatus(foundOnly), "Не проверено");
    assert.equal(equipmentDocumentStatus({ ...read, document: { ...read.document, revision_ambiguous: true } }), "Требуется уточнение редакции");
    component.dispose();
  } finally { environment.restore(); }
});

test("аналог явно подписан и не представлен подтверждённым соответствием проекта", () => {
  const environment = fixture();
  try {
    const row = { model: "MODEL-1", status: "confirmed", manufacturer_site: { name: "Другой завод", url: "https://other.example", relationship: "analog" },
      document: { source_id: "EQ-" + "b".repeat(32) }, metrics: {} };
    const component = appendEquipmentReport(new FakeElement(), { models: [row] }, { autoUpdates: false });
    assert.ok(collect(component.root).some((node) => node.textContent === "Аналог — производитель проектного изделия не установлен"));
    assert.equal(equipmentDocumentStatus(row), "Недостаточно данных"); component.dispose();
  } finally { environment.restore(); }
});

test("ссылка производителя не подставляется из URL проверенного дилерского документа", () => {
  const environment = fixture();
  try {
    const component = appendEquipmentReport(new FakeElement(), { models: [{ manufacturer: "Завод", model: "MODEL-1",
      document: { source_id: "EQ-" + "b".repeat(32), final_url: "https://dealer.example/manual.pdf", trust_status: "trusted" }, metrics: {} }] }, { autoUpdates: false });
    assert.ok(collect(component.root).some((node) => node.textContent === "Не найден"));
    component.dispose();
  } finally { environment.restore(); }
});

test("URL с исполняемой схемой, credentials и локальным адресом не попадает в DOM", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,test", "https://user:password@example.com", "http://localhost./", "http://2130706433/", "https://192.168.1.1/", "https://[::1]/"]) assert.equal(safeExternalSourceUrl(value), null);
  assert.equal(safeExternalSourceUrl("https://manufacturer.example/manual.pdf"), "https://manufacturer.example/manual.pdf");
});

test("переход ждёт изображение и подсвечивает только заданный finding_id", async () => {
  const environment = fixture();
  try {
    const page = new FakeElement(); page.scrollIntoView = () => { page.scrolled = true; };
    const image = new FakeElement("img"); image.className = "analysis-result__page-image"; image.complete = false;
    const chosen = new FakeElement(); chosen.className = "analysis-result__bbox"; chosen.dataset.findingId = "f1";
    const other = new FakeElement(); other.className = "analysis-result__bbox"; other.dataset.findingId = "f2";
    page.append(image, chosen, other); globalThis.document.getElementById = () => page;
    const pending = focusPageEvidence(2, { findingId: "f1" });
    assert.ok(page.scrolled); assert.equal(image.loading, "eager"); assert.ok(!chosen.classList.contains("is-document-evidence-focus"));
    image.complete = true; image.naturalWidth = 200; image.dispatch("load"); await pending;
    assert.ok(chosen.classList.contains("is-document-evidence-focus")); assert.ok(!other.classList.contains("is-document-evidence-focus"));
    await focusPageEvidence(2, { findingId: "absent" });
    assert.equal(page.querySelector(".analysis-result__evidence-navigation-message").textContent, "Точное место не определено");
  } finally { environment.restore(); }
});

test("ошибка изображения не выдаёт старую область за найденное место", async () => {
  const environment = fixture();
  try {
    const page = new FakeElement(); page.scrollIntoView = () => {};
    const image = new FakeElement("img"); image.className = "analysis-result__page-image";
    image.complete = true; image.naturalWidth = 0;
    const box = new FakeElement(); box.className = "analysis-result__bbox"; box.dataset.findingId = "f1";
    page.append(image, box); globalThis.document.getElementById = () => page;
    assert.equal(await focusPageEvidence(22, { findingId: "f1" }), false);
    assert.ok(!box.classList.contains("is-document-evidence-focus"));
    assert.match(page.querySelector(".analysis-result__evidence-navigation-message").textContent, /не загрузилось/);
  } finally { environment.restore(); }
});
