// frontend/tests/equipment-discovery.test.js

/** Доступные состояния EQ, самостоятельные выгрузки и безопасная диагностика. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { appendEquipmentReport, equipmentRows, equipmentDocumentStatus } from "../src/js/features/analysis/equipment-report.js";

const collect = (node) => [node, ...node.children.flatMap(collect)];

test("каждая позиция сохраняет свою идентичность, а недоступный поиск не означает отсутствие документа", () => {
  const positions = [{ equipment_id: "sand-1", item_type: "material", stage_status: "search_temporarily_unavailable" }, { equipment_id: "sand-2", item_type: "material" }];
  assert.deepEqual(equipmentRows({ positions, models: [] }), positions);
  assert.equal(equipmentDocumentStatus(positions[0]), "Поиск временно недоступен");
  assert.equal(equipmentDocumentStatus({ document: { source_id: "EQ-x", applicability: "technical_reference" }, status: "confirmed" }), "Справочник; поставка не подтверждена");
});

test("кандидат, сравнение и кнопки PDF/ZIP видны без доступа к серверным журналам", async () => {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  try {
    const row = { equipment_id: "pipe-1", specification_caption: "Труба ПЭ100 SDR11", item_type: "standard_product", stage_status: "document_rejected",
      candidates: [{ candidate_id: "EQC-1", title: "<img src=x>", document_url: "javascript:alert(1)", applicability: "wrong_model", inspection_status: "rejected" }],
      comparisons: [{ project_value: "110", project_unit: "мм", manufacturer_value: "90", manufacturer_unit: "мм", source_page: 2, explanation: "Другая модель" }] };
    const component = appendEquipmentReport(new FakeElement(), { positions: [row], revision: 7, search_status: "incomplete" }, { jobId: "job", autoUpdates: false });
    let nodes = collect(component.root);
    assert.ok(nodes.some((node) => node.textContent === "Скачать отчёт EQ (PDF)"));
    assert.ok(nodes.some((node) => node.textContent === "Скачать документы EQ (ZIP)"));
    nodes.find((node) => node.textContent === "Подробности проверки" && node.tagName === "BUTTON").click();
    nodes = collect(component.root);
    assert.ok(nodes.some((node) => node.textContent.includes("Другая модель")));
    assert.ok(nodes.some((node) => node.textContent.includes("проект") || node.textContent.includes("Проект: 110")));
    assert.equal(nodes.filter((node) => node.href?.startsWith("javascript:")).length, 0);
    assert.equal(nodes.filter((node) => node.tagName === "IMG").length, 0);
    component.dispose();
  } finally { globalThis.document = previous; }
});
