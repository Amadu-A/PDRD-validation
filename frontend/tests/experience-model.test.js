// frontend/tests/experience-model.test.js

/** Проверяет рабочие фильтры и локальную правку демонстрации Experience. */
import assert from "node:assert/strict";
import test from "node:test";
import { createExperienceModel } from "../src/js/features/experience/model.js";

test("фильтры ищут текст, документ, тег, решение и активность", () => {
  const model = createExperienceModel();
  assert.equal(model.list().length, 2);
  assert.equal(model.list({ query: "ДВЕРИ" })[0].id, "demo-wise");
  assert.equal(model.list({ tag: "edited", decision: "rejected",
    active: "false" })[0].id, "demo-edited");
  assert.deepEqual(model.list({ tag: "gold" }), []);
});

test("правка меняет только разрешённые данные локальной копии", () => {
  const model = createExperienceModel();
  const before = model.get("demo-edited");
  const edited = model.update("demo-edited", {
    text: "  Исправлено  ",
    normative_basis: "  Раздел 8  ",
    active: true,
  });
  assert.equal(edited.text, "Исправлено");
  assert.equal(edited.normative_basis, "Раздел 8");
  assert.equal(edited.active, true);
  assert.equal(edited.tag, before.tag);
  assert.equal(edited.decision, before.decision);
  assert.equal(edited.learning_use, "needs_adjudication");
  assert.equal(createExperienceModel().get("demo-edited").text, before.text);
});

test("ошибочные изменения отклоняются без порчи записи", () => {
  const model = createExperienceModel();
  const before = model.get("demo-wise");
  assert.throws(() => model.update("missing", {
    text: "Найдено", normative_basis: "", active: true,
  }));
  assert.throws(() => model.update("demo-wise", {
    text: " ", normative_basis: "", active: true,
  }));
  assert.throws(() => model.update("demo-wise", {
    text: "Корректный текст", normative_basis: "", active: "true",
  }));
  assert.deepEqual(model.get("demo-wise"), before);
});
