// frontend/tests/review-restore.test.js

/** Регрессии повторной загрузки отчёта и защиты несохранённого Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { bindReportRestoration } from "../src/js/features/analysis/restore.js";
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
