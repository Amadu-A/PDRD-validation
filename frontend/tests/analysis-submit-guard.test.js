// frontend/tests/analysis-submit-guard.test.js

/** Проверяет независимые блокировки запуска и доступную подсказку причины. */
import assert from "node:assert/strict";
import test from "node:test";
import { File } from "node:buffer";
import { setAnalysisSubmitBlock } from "../src/js/features/analysis/submit-guard.js";
import { createTechnicalAssignmentFilePicker } from "../src/js/features/technical_assignment/file.js";
import { FakeElement } from "./helpers/fake-dom.js";

/** Подключает настоящий обработчик выбора раздела к DOM и управляемому HTTP. */
function pickerFixture(t) {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  t.after(() => { Object.assign(globalThis, previous); });
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.window = { clearTimeout() {}, setTimeout() { return 1; } };
  const requests = [];
  globalThis.fetch = (url, options) => new Promise((resolve) => {
    requests.push({ url, options, respond: (payload) => resolve(Response.json(payload)) });
  });
  const root = new FakeElement();
  const section = new FakeElement();
  root.selectors.set("[data-specification-placeholder]", section);
  for (const selector of ["#specificationFile", "[data-technical-assignment-upload-zone]",
    ".normative-sidebar__file-picker-name", "[data-technical-assignment-badge]",
    "[data-technical-assignment-clear]", ".normative-sidebar__block-hint"]) {
    section.selectors.set(selector, new FakeElement());
  }
  section.querySelector(".normative-sidebar__file-picker-name").insertAdjacentElement = (
    _position, element,
  ) => { section.append(element); };
  const input = section.querySelector("#specificationFile");
  input.files = [];
  const wrapper = new FakeElement("span");
  wrapper.dataset.analysisSubmitGuard = "";
  const button = new FakeElement("button");
  button.parentElement = wrapper;
  const picker = createTechnicalAssignmentFilePicker(root, { submitButton: button });
  picker.bind();
  return { picker, input, button, wrapper, requests };
}

test("снятие блокировки ТЗ и закрытие modal не разрешают запуск без раздела", () => {
  const wrapper = {};
  const button = { parentElement: wrapper };
  setAnalysisSubmitBlock(button, "section", "Выберите раздел, чтобы запустить анализ.");
  setAnalysisSubmitBlock(button, "technical_assignment", "ТЗ индексируется.");
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "technical_assignment");
  setAnalysisSubmitBlock(button, "analysis");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Выберите раздел, чтобы запустить анализ.");
  assert.equal(wrapper.title, button.title);
  setAnalysisSubmitBlock(button, "section");
  assert.equal(button.disabled, false);
  assert.equal(wrapper.title, "");
});

test("смена раздела во время анализа сохраняет блокировку активной работы", () => {
  const button = {};
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "section", "Выберите раздел.");
  setAnalysisSubmitBlock(button, "section");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Анализ выполняется.");
});

test("выбор раздела без необязательного ТЗ разрешает анализ и убирает подсказку", async (t) => {
  const { picker, button, wrapper, requests } = pickerFixture(t);
  const message = "Выберите раздел, чтобы запустить анализ.";
  assert.equal(button.disabled, true);
  assert.equal(wrapper.title, message);
  assert.equal(wrapper.tabIndex, 0);

  await picker.setSection("ИОС2");
  assert.equal(button.disabled, false);
  assert.equal(button.title, "");
  assert.equal(wrapper.title, "");
  assert.equal(wrapper.tabIndex, -1);
  assert.equal(wrapper.getAttribute("aria-label"), "Запуск анализа");

  await picker.setSection("ИОС3");
  assert.equal(button.disabled, false);
  await picker.setSection(null);
  assert.equal(button.disabled, true);
  assert.equal(button.title, message);
  assert.equal(wrapper.title, message);
  assert.equal(wrapper.tabIndex, 0);
  assert.equal(wrapper.getAttribute("aria-label"), message);
  await picker.setSection("ИОС2");
  assert.equal(button.disabled, false);
  assert.equal(requests.length, 0);
});

test("выбор раздела без ТЗ не снимает блокировку выполняющегося анализа", async (t) => {
  const { picker, button } = pickerFixture(t);
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  await picker.setSection("ИОС2");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Анализ выполняется.");
  await picker.setSection(null);
  assert.match(button.title, /Выберите раздел/);
  await picker.setSection("ИОС3");
  assert.equal(button.title, "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "analysis");
  assert.equal(button.disabled, false);
});

test("с выбранным ТЗ анализ ждёт подготовки для текущего раздела", async (t) => {
  const { picker, input, button, wrapper, requests } = pickerFixture(t);
  input.files = [new File(["Техническое задание"], "task.pdf", { type: "application/pdf" })];
  input.dispatch("change");
  assert.equal(button.disabled, true);
  assert.equal(input.dataset.indexStatus, "waiting_section");
  assert.equal(requests.length, 0);

  const prepared = { technical_assignment_id: "assignment", analysis_document_id: "document",
    section_id: "ИОС2", index_status: "ready" };
  const firstPreparation = picker.setSection("ИОС2");
  assert.equal(button.disabled, true);
  assert.doesNotMatch(button.title, /Выберите раздел/);
  assert.match(wrapper.title, /Подождите, пока техническое задание/);
  assert.equal(requests[0].options.body.get("section_id"), "ИОС2");
  requests[0].respond(prepared);
  await firstPreparation;
  assert.equal(button.disabled, false);
  assert.equal(input.dataset.preparedSectionId, "ИОС2");

  const secondPreparation = picker.setSection("ИОС3");
  assert.equal(button.disabled, true);
  assert.equal(input.dataset.preparedSectionId, undefined);
  assert.equal(requests[1].options.body.get("section_id"), "ИОС3");
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  requests[1].respond({ ...prepared, section_id: "ИОС3" });
  await secondPreparation;
  assert.equal(input.dataset.preparedSectionId, "ИОС3");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "analysis");
  assert.equal(button.disabled, false);
});
