// frontend/tests/analysis-errors.test.js

/** Пользовательский отказ PDF показывает причину и действие без технического HTTP-кода. */
import assert from "node:assert/strict";
import test from "node:test";
import { ApiError } from "../src/js/api.js";
import { createResultView } from "../src/js/components/result.js";
import { FakeElement } from "./helpers/fake-dom.js";

test("превышение страниц показывает русский текст без кода HTTP и сохраняет обычные ошибки", () => {
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  const view = createResultView(root);
  const detail = "Выбрано 201 страниц. За одну проверку можно обработать не более 200 страниц. Укажите диапазон в поле «Страницы PDF».";
  view.showError(new ApiError(422, detail));
  assert.equal(root.children[0].textContent, `Ошибка:\n${detail}`);
  assert.doesNotMatch(root.children[0].textContent, /HTTP|AnalysisOrchestrationError|n8n/);
  view.showError(new Error("Не удалось прочитать результат"));
  assert.match(root.children[0].textContent, /Не удалось прочитать результат/);
});
