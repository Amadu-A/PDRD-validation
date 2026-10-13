// frontend/tests/help-settings.test.js

/** Матрица справки проверяется на настоящей форме и отправляемых данных. */
import assert from "node:assert/strict";
import { File } from "node:buffer";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createAnalysisForm } from "../src/js/features/analysis/form.js";
import { ANALYSIS_SETTINGS, analysisSettingAvailability } from "../src/js/features/help/settings.js";
import { FakeElement } from "./helpers/fake-dom.js";

for (const mode of ["empty", "pdf_only", "cad_only", "pdf_cad"]) {
  for (const enabled of [false, true]) {
    test(`${mode}, EQ=${enabled}: описание и форма имеют одинаковую доступность`, (t) => {
      const previous = globalThis.document;
      t.after(() => { globalThis.document = previous; });
      globalThis.document = { createElement: (tag) => new FakeElement(tag) };
      const input = () => Object.assign(new FakeElement("input"), { files: [], checked: true,
        setCustomValidity() {}, reportValidity() { return true; } });
      const pdfInput = input(), cadInput = input(), start = input(), end = input();
      if (mode === "pdf_only" || mode === "pdf_cad") pdfInput.files = [new File(["pdf"], "project.pdf")];
      if (mode === "cad_only" || mode === "pdf_cad") cadInput.files = [new File(["cad"], "project.dxf")];
      start.value = "1"; end.value = "2";
      start.parentElement = { parentElement: new FakeElement() };
      const settings = Object.fromEntries(ANALYSIS_SETTINGS.map((setting) => [setting.id, input()]));
      settings["equipment-search"].checked = enabled;
      const form = createAnalysisForm({ formElement: new FakeElement("form"), pdfInput, cadInput,
        technicalAssignmentInput: input(), pagesInput: input(), pagesHint: new FakeElement(),
        useExplanatoryNoteInput: settings["explanatory-note"], documentContextInput: settings["document-context"],
        equipmentSearchInput: settings["equipment-search"], unverifiedSourcesInput: settings["unverified-sources"],
        noteStartPageInput: start, noteEndPageInput: end });
      form.sync();
      const expected = analysisSettingAvailability(mode, { equipmentEnabled: enabled });
      for (const setting of ANALYSIS_SETTINGS) assert.equal(settings[setting.id].disabled, !expected[setting.id]);
      const payload = form.toFormData();
      assert.equal(payload.get("use_document_context"), String(mode === "pdf_only"));
      assert.equal(payload.get("use_equipment_web_search"), String(mode === "pdf_only" && enabled));
      assert.equal(payload.get("allow_unverified_equipment_sources"), String(mode === "pdf_only" && enabled));
      assert.equal(payload.has("use_explanatory_note"), mode === "pdf_only" || mode === "pdf_cad");
    });
  }
}

test("названия подсказок совпадают с реальными четырьмя переключателями HTML", () => {
  const html = readFileSync(new URL("../src/index.html", import.meta.url), "utf8");
  for (const setting of ANALYSIS_SETTINGS) {
    const start = html.indexOf(`data-setting-help="${setting.id}"`);
    assert.ok(start >= 0);
    const label = html.slice(start).split("</label>")[0];
    assert.ok(label.includes(`data-${setting.hook}`));
    assert.ok(label.includes(setting.label));
  }
  assert.equal((html.match(/type="checkbox"/g) || []).length, 4);
});

test("D и EQ независимы, а ПЗ может быть отключена конфигурацией", () => {
  assert.equal(analysisSettingAvailability("pdf_only", { equipmentEnabled: false })["document-context"], true);
  assert.equal(analysisSettingAvailability("pdf_only", { explanatoryEnabled: false })["explanatory-note"], false);
  assert.equal(analysisSettingAvailability("pdf_cad", { equipmentEnabled: true })["unverified-sources"], false);
});
