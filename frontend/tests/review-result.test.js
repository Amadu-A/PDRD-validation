// frontend/tests/review-result.test.js

/** Проверяет подключение review через стабильный hook Result View. */

import assert from "node:assert/strict";
import test from "node:test";
import { createResultView } from "../src/js/components/result.js";

test("review монтируется только после отображения готового отчёта", () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement() { return { className: "", textContent: "" }; },
  };
  try {
    const element = {
      children: [],
      replaceChildren(...children) { this.children = children; },
    };
    const calls = [];
    const result = createResultView(element, {
      onReportRendered: (root) => calls.push(root),
    });
    result.show("Анализ выполняется");
    result.showError(new Error("Ошибка запроса"));
    assert.equal(calls.length, 0);
    const fragment = { kind: "completed-report" };
    result.showReport(fragment);
    assert.deepEqual(element.children, [fragment]);
    assert.deepEqual(calls, [element]);
  } finally {
    globalThis.document = originalDocument;
  }
});