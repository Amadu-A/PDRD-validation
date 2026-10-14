// frontend/tests/analysis-form.test.js

/** Регрессии обязательного раздела PDF/CAD и сериализации пользовательских пакетов. */
import assert from "node:assert/strict";
import test from "node:test";
import { File } from "node:buffer";
import { createAnalysisController } from "../src/js/features/analysis/controller.js";
import { createAnalysisForm } from "../src/js/features/analysis/form.js";
import { FakeElement } from "./helpers/fake-dom.js";

/** Создаёт входы настоящего controller с минимальным DOM для диапазона ПЗ. */
function buildForm(t, selection, kind = "pdf", documentContextInput = null, equipmentSearchInput = null, unverifiedSourcesInput = null, elements = {}) {
  const previousDocument = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  t.after(() => { globalThis.document = previousDocument; });
  const input = () => Object.assign(new FakeElement("input"), {
    files: [], checked: false, setCustomValidity(value) { this.error = value; },
    reportValidity() { return !this.error; },
  });
  const pdfInput = input();
  const cadInput = input();
  (kind === "pdf" ? pdfInput : cadInput).files = [new File(["drawing"], kind === "pdf" ? "drawing.pdf" : "drawing.dxf")];
  Object.assign(elements, { pdfInput, cadInput });
  const noteStart = input();
  noteStart.parentElement = { parentElement: new FakeElement() };
  return createAnalysisForm({ formElement: new FakeElement("form"), pdfInput, cadInput,
    technicalAssignmentInput: input(), pagesInput: input(), pagesHint: new FakeElement(),
    documentContextInput, equipmentSearchInput, unverifiedSourcesInput, useExplanatoryNoteInput: input(), noteStartPageInput: noteStart, noteEndPageInput: input(),
    getNormativeSelection: () => selection,
  });
}

for (const kind of ["pdf", "cad"]) {
  test(`${kind}: анализ без раздела блокируется до HTTP-запроса`, (t) => {
    const form = buildForm(t, null, kind);
    assert.deepEqual(form.validate(), { valid: false, message: "Выберите раздел, чтобы запустить анализ." });
    const body = form.toFormData();
    assert.equal(body.has(kind), true);
    assert.equal(body.has("normative_section_id"), false);
    assert.equal(body.has("user_package_document_ids"), false);
  });
}

test("пустой список пакетов не передаётся даже с выбранным разделом", (t) => {
  const form = buildForm(t, { sectionId: "section", documentIds: [], userPackageDocumentIds: [] });
  assert.equal(form.toFormData().has("user_package_document_ids"), false);
});

test("явный непустой выбор пакетов сериализуется отдельно", (t) => {
  const form = buildForm(t, { sectionId: "section", documentIds: [], userPackageDocumentIds: ["document"] });
  assert.deepEqual(JSON.parse(form.toFormData().get("user_package_document_ids")), ["document"]);
});


test("анализ удаляемого раздела блокируется до отправки запроса", (t) => {
  const form = buildForm(t, { sectionId: "section", documentIds: [], deleting: true });
  assert.deepEqual(form.validate(), { valid: false, message: "Раздел удаляется. Выберите другой раздел." });
});


test("режим всего PDF по умолчанию выключен и включается отдельным чекбоксом", (t) => {
  const toggle = Object.assign(new FakeElement("input"), { checked: false, disabled: false });
  const form = buildForm(t, null, "pdf", toggle);
  assert.equal(form.toFormData().get("use_document_context"), "false");
  toggle.checked = true;
  assert.equal(form.toFormData().get("use_document_context"), "true");
});

test("переключатель D выключается при смене режима на CAD", (t) => {
  const toggle = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  const form = buildForm(t, null, "cad", toggle);
  form.validate();
  assert.equal(form.toFormData().get("use_document_context"), "false");
});


test("EQ отключён по умолчанию; unsafe зависит от EQ и не сохраняется", (t) => {
  const equipment = Object.assign(new FakeElement("input"), { checked: false, disabled: false });
  const unsafe = Object.assign(new FakeElement("input"), { checked: false, disabled: true });
  const form = buildForm(t, { sectionId: "section", documentIds: [] }, "pdf", null, equipment, unsafe);
  form.sync();
  assert.equal(form.toFormData().get("use_equipment_web_search"), "false");
  equipment.checked = true;
  form.sync();
  assert.equal(unsafe.disabled, false);
  unsafe.checked = true;
  assert.equal(form.toFormData().get("allow_unverified_equipment_sources"), "true");
  form.resetUnverifiedSources();
  assert.equal(unsafe.checked, false);
  equipment.checked = false;
  form.sync();
  assert.equal(unsafe.disabled, true);
  assert.equal(form.toFormData().get("allow_unverified_equipment_sources"), "false");
});

test("EQ в CAD режиме недоступен", (t) => {
  const equipment = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  const unsafe = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  const form = buildForm(t, null, "cad", null, equipment, unsafe);
  form.sync();
  assert.equal(equipment.checked, false);
  assert.equal(equipment.disabled, true);
  assert.equal(unsafe.checked, false);
  assert.equal(form.toFormData().get("use_equipment_web_search"), "false");
});


test("после принятия анализа unsafe остаётся виден и передаётся в запрос", async (t) => {
  const equipment = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  const unsafe = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  const form = buildForm(t, { sectionId: "section", documentIds: [] }, "pdf", null, equipment, unsafe);
  form.bind();
  const previous = { fetch: globalThis.fetch, window: globalThis.window };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.window = { location: { href: "https://pdrd.example/" },
    history: { replaceState() {} } };
  let submitted;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/v1/analyses") {
      submitted = options.body;
      return new Response(JSON.stringify({ job_id: "eq-unsafe-regression", status: "queued" }));
    }
    return new Response(JSON.stringify({ status: "cancelled" }));
  };
  const errors = [];
  const controller = createAnalysisController({ analysisForm: form,
    modal: { clearJobId() {}, show() {}, hide() {}, setJobId() {}, setCancelling() {}, setCancelHandler() {} },
    resultView: { show() {}, showError: (error) => errors.push(error) },
  });
  await controller.submit({ preventDefault() {} });
  assert.deepEqual(errors, []);
  assert.equal(submitted.get("use_equipment_web_search"), "true");
  assert.equal(submitted.get("allow_unverified_equipment_sources"), "true");
  assert.equal(unsafe.checked, true);
  form.sync();
  assert.equal(unsafe.checked, true);
});

for (const inputName of ["pdfInput", "cadInput"]) {
  test(`смена ${inputName} снимает разрешение неподтверждённых источников`, (t) => {
    const equipment = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
    const unsafe = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
    const elements = {};
    const form = buildForm(t, null, "pdf", null, equipment, unsafe, elements);
    form.bind();
    elements[inputName].dispatch("change");
    assert.equal(unsafe.checked, false);
    assert.equal(form.toFormData().get("allow_unverified_equipment_sources"), "false");
  });
}
