// frontend/tests/workflow-cancellation.test.js

/** Исполняет реальные IF-выражения workflow для штатной отмены и обычного результата. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

for (const mode of ["pdf", "cad", "pdf-cad"]) {
  const workflow = JSON.parse(fs.readFileSync(new URL(`../../n8n/workflows/analysis-v2-${mode}.json`, import.meta.url), "utf8"));
  test(`${mode}: отменённый HTTP-этап не попадает в финализацию и сборку результата`, () => {
    const guards = workflow.nodes.filter((node) => node.name.startsWith("Continue ") && node.name.endsWith(" Response"));
    assert.ok(guards.length >= 4);
    for (const guard of guards) {
      const condition = guard.parameters.conditions.conditions[0];
      for (const cancelled of [true, false, undefined]) {
        const json = {cancelled, items: []};
        const actual = vm.runInNewContext(condition.leftValue.slice(3, -2), {
          $json: json, $input: {all: () => [{json}]},
        }, { timeout: 500 });
        assert.equal(condition.operator.operation, "notEquals");
        const output = actual !== condition.rightValue ? 0 : 1;
        const targets = workflow.connections[guard.name].main[output].map((entry) => entry.node);
        if (cancelled) assert.deepEqual(targets, ["Build Cancelled Result"]);
        else assert.ok(!targets.includes("Build Cancelled Result"));
      }
    }
  });
}
