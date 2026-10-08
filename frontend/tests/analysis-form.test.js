// frontend/tests/analysis-form.test.js

/** Регрессия обычного PDF/CAD без обязательного раздела и пустого поля пакетов. */
import assert from "node:assert/strict";
import test from "node:test";
import { File } from "node:buffer";
import { createAnalysisForm } from "../src/js/features/analysis/form.js";
import { FakeElement } from "./helpers/fake-dom.js";

/** Создаёт входы настоящего controller с минимальным DOM для диапазона ПЗ. */
function buildForm(t, selection, kind = "pdf", documentContextInput = null) {
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
  const noteStart = input();
  noteStart.parentElement = { parentElement: new FakeElement() };
  return createAnalysisForm({ formElement: new FakeElement("form"), pdfInput, cadInput,
    technicalAssignmentInput: input(), pagesInput: input(), pagesHint: new FakeElement(),
    documentContextInput, useExplanatoryNoteInput: input(), noteStartPageInput: noteStart, noteEndPageInput: input(),
    getNormativeSelection: () => selection,
  });
}

for (const kind of ["pdf", "cad"]) {
  test(`${kind}: обычный анализ разрешён без нормативного раздела`, (t) => {
    const form = buildForm(t, null, kind);
    assert.equal(form.validate().valid, true);
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
  assert.deepEqual(form.validate(), { valid: false, message: "Раздел удаляется. Выберите другой раздел или анализ без раздела." });
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
