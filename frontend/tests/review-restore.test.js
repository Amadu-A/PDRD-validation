// frontend/tests/review-restore.test.js

/** Регрессии повторной загрузки отчёта и защиты несохранённого Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { bindReportRestoration, restoreAnalysisReport } from "../src/js/features/analysis/restore.js";
import { newManualFindingId } from "../src/js/features/review/identity.js";

test("несохранённый Review блокирует новое задание, после сохранения submit работает", () => {
  const previousWindow = globalThis.window;
  globalThis.window = { location: { href: "http://localhost/" } };
  try {
    let handler;
    let pending = true;
    let calls = 0;
    const formElement = { addEventListener: (_name, action) => { handler = action; } };
    bindReportRestoration({ resultView: {}, formElement, canSubmit: () => !pending,
      submit: () => { calls += 1; } });
    let prevented = false;
    handler({ preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(calls, 0);
    pending = false;
    handler({ preventDefault: () => {} });
    assert.equal(calls, 1);
  } finally { globalThis.window = previousWindow; }
});

test("Gold получает допустимый UUID даже без randomUUID в браузере", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
    const values = Array.from({ length: 20 }, newManualFindingId);
    assert.equal(new Set(values).size, values.length);
    for (const value of values) {
      assert.match(value, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    }
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
    else delete globalThis.crypto;
  }
});


test("старый отчёт после удаления исходников открывается без PNG, PDF и повторного анализа", async (t) => {
  const previous = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  const jobId = "44444444-4444-4444-8444-444444444444";
  globalThis.window = { location: { href: `https://pdrd.itcneoterm.local/?job_id=${jobId}` },
    history: { replaceState() {} } };
  globalThis.document = { createElement: (tag) => new FakeElement(tag),
    createDocumentFragment: () => new FakeElement("fragment") };
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options?.method || "GET" });
    const result = url.endsWith("/result") ? {
      status: "completed", source_mode: "pdf_only", file_name: "Старый.pdf", findings: [],
      source_artifacts_expired: true,
      source_artifacts_message: "Исходники удалены через 30 дней. Human Review сохранён.",
    } : { status: "completed" };
    return new Response(JSON.stringify(result), { headers: { "content-type": "application/json" } });
  };
  let rendered, failure;
  await restoreAnalysisReport({ show() {}, showReport(report) { rendered = report; },
    showError(error) { failure = error; } });
  assert.equal(failure, undefined);
  assert.match(rendered.children[0].textContent, /Human Review сохранён/);
  assert.equal(rendered.querySelectorAll(".analysis-export").length, 0);
  assert.deepEqual(requests, [
    { url: `/api/v1/analyses/${jobId}`, method: "GET" },
    { url: `/api/v1/analyses/${jobId}/progress`, method: "GET" },
    { url: `/api/v1/analyses/${jobId}/result`, method: "GET" },
  ]);
});
