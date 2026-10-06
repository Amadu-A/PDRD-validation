// frontend/tests/normative-section-selection.test.js

/** Регрессия: открытие каталога не запускает анализ первого раздела случайно. */
import assert from "node:assert/strict";
import test from "node:test";

import {
  retainSectionSelection,
  sectionSelectionError,
} from "../src/js/features/normative/section-selection.js";

const sections = [
  { section_id: "AR" },
  { section_id: "KR" },
];

test("каталог оставляет раздел пустым при первом открытии", () => {
  assert.equal(retainSectionSelection(sections, null), null);
  assert.equal(sectionSelectionError(null), "Выберите раздел проектной документации");
});

test("повторная загрузка сохраняет только существующий явный выбор", () => {
  assert.equal(retainSectionSelection(sections, "KR"), "KR");
  assert.equal(retainSectionSelection(sections, "deleted"), null);
});

test("созданный раздел выбирается явно после создания", () => {
  assert.equal(retainSectionSelection(sections, null, "KR"), "KR");
  assert.equal(sectionSelectionError({ sectionId: "KR" }), null);
});
