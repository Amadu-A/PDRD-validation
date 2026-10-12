// frontend/tests/equipment-report.test.js

/** Фильтры, строгая семантика цветов и безопасный текст оборудования. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { appendEquipmentReport, equipmentModels, filterEquipmentModels } from "../src/js/features/analysis/equipment-report.js";

function collect(node) { return [node, ...node.children.flatMap(collect)]; }

test("таблица EQ показывает модели и фильтрует по техническому результату", () => {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  try {
    const parent = new FakeElement();
    const models = [
      { model: "Сигнал-10", variant: "2RS485", variant_kind: "interface", status: "incomplete", metrics: { queries: 1, downloads: 1 }, reason_code: "tls_error" },
      { manufacturer: "РИЭЛТА", model: "ИО 309-30", variant: "Пирон-Ш", variant_kind: "model_name", status: "confirmed", metrics: {} },
      { model: "РИП-12", variant: "исп. 51", variant_kind: "execution", status: "mismatch", metrics: {} },
      { model: "<img src=x onerror=alert(1)>", status: "not_checked", metrics: {} },
    ];
    const component = appendEquipmentReport(parent, { models }, { autoUpdates: false });
    let nodes = collect(component.root);
    assert.equal(nodes.filter((node) => node.tagName === "TR").length, 5);
    assert.ok(nodes.some((node) => node.textContent === "Сигнал-10 2RS485"));
    assert.ok(nodes.some((node) => node.textContent === "ИО 309-30 Пирон-Ш"));
    assert.ok(nodes.some((node) => node.textContent === "исп. 51"));
    assert.ok(nodes.some((node) => node.textContent === "Ошибка TLS"));
    assert.ok(nodes.some((node) => node.className.endsWith("--incomplete")));
    assert.equal(nodes.filter((node) => node.tagName === "IMG").length, 0);
    const filter = nodes.find((node) => node.textContent === "Несоответствие 1");
    filter.dispatch("click");
    nodes = collect(component.root);
    assert.equal(nodes.filter((node) => node.tagName === "TR").length, 2);
    assert.ok(nodes.some((node) => node.className.endsWith("--mismatch")));
    component.dispose();
  } finally { globalThis.document = previous; }
});

test("старый отчёт не превращает resolved идентификацию в успешную проверку", () => {
  const rows = equipmentModels({ inventory: [{ model: "NXB-63", status: "resolved", occurrences: [{ page: 1 }, { page: 2 }] }] });
  assert.equal(rows[0].status, "not_checked");
  assert.deepEqual(rows[0].pages, [1, 2]);
  assert.equal(filterEquipmentModels(rows, "confirmed").length, 0);
});

test("синтетические 500 моделей остаются доступны во всех фильтрах", () => {
  const models = Array.from({ length: 500 }, (_, index) => ({ model: `MODEL-${index}`, status: index < 300 ? "incomplete" : "not_checked" }));
  assert.equal(filterEquipmentModels(models, "all").length, 500);
  assert.equal(filterEquipmentModels(models, "incomplete").length, 300);
  assert.equal(filterEquipmentModels(models, "not_checked").length, 200);
});
