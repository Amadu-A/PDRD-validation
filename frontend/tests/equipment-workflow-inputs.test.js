// frontend/tests/equipment-workflow-inputs.test.js

/** Проверяет дедупликацию EQ-входов тем же кодом, что исполняет n8n. */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const workflow = JSON.parse(readFileSync(
  new URL("../../n8n/workflows/analysis-v2-pdf.json", import.meta.url),
  "utf8",
));
const node = workflow.nodes.find((item) => item.name === "Build Equipment Search Inputs");

test("одна модель ищется один раз, ненадёжное вхождение запрещает поиск", () => {
  const pages = [{
    result: { facts: { equipment_identities: [
      { manufacturer: "MEAN WELL", model: "DRC-100B", variant: "",
        status: "resolved", confidence: 0.97,
        parameters: [{ property_name: "output_voltage" }] },
    ] } },
  }, {
    result: { facts: { equipment_identities: [
      { manufacturer: "mean well", model: "DRC-100B", variant: "",
        status: "needs_review", confidence: 0.6,
        parameters: [{ property_name: "output_current" }] },
    ] } },
  }];
  const code = new Function("$", node.parameters.jsCode);
  const result = code(() => ({ first: () => ({ json: { items: pages } }) }));

  assert.equal(result[0].json.identities.length, 1);
  assert.equal(result[0].json.identities[0].status, "needs_review");
  assert.equal(result[0].json.identities[0].confidence, 0.6);
  assert.deepEqual(
    result[0].json.identities[0].properties,
    ["output_voltage", "output_current"],
  );
});

test("выключенный EQ обходит поиск и сохраняет обычный маршрут", () => {
  const targets = (name, branch = 0) =>
    workflow.connections[name].main[branch].map((edge) => edge.node);
  assert.deepEqual(targets("Use Equipment Search", 1), ["Use Document Context"]);
  assert.deepEqual(targets("Use Equipment Results", 1), ["Restore Equipment Pages"]);
  assert.deepEqual(targets("Gate Collect Finalization Stage"), ["Finalize Findings"]);
});


test("повреждённый список параметров не останавливает основной анализ", () => {
  const pages = [{
    result: { facts: { equipment_identities: [{
      manufacturer: "CHINT", model: "NXB-63", variant: "",
      status: "resolved", confidence: 0.9, parameters: "unexpected",
    }] } },
  }];
  const code = new Function("$", node.parameters.jsCode);
  const result = code(() => ({ first: () => ({ json: { items: pages } }) }));
  assert.deepEqual(result[0].json.identities[0].properties, []);
});
