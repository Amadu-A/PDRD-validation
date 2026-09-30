// frontend/tests/experience-model.test.js

/** Проверяет рабочие фильтры и локальную правку демонстрации Experience. */
import assert from "node:assert/strict";
import test from "node:test";
import { experienceTagLabel } from "../src/js/features/experience/labels.js";
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

test("Edited разделяется по решению в фильтре, сохраняя тег и needs_adjudication", () => {
  const demo = createExperienceModel().get("demo-edited");
  const model = createExperienceModel([
    demo,
    { ...demo, id: "positive-edited", decision: "accepted", learning_use: "positive" },
    { ...demo, id: "pending-edited", decision: "pending", learning_use: null },
  ]);
  for (const decision of ["accepted", "rejected", "pending"]) {
    const rows = model.list({ tag: `edited:${decision}` });
    assert.equal(rows.length, 1);
    assert.equal(rows[0].tag, "edited");
    assert.equal(rows[0].decision, decision);
  }
  assert.equal(model.list({ tag: "edited" }).length, 3);
  assert.deepEqual(model.list({ tag: "edited:accepted", decision: "rejected" }), []);
  assert.deepEqual(model.list({ tag: "gold:accepted" }), []);
  assert.equal(model.list({ tag: "edited:rejected" })[0].learning_use, "needs_adjudication");
  assert.equal(experienceTagLabel("edited", "accepted"), "Edited — Принято");
  assert.equal(experienceTagLabel("edited", "rejected"), "Edited — Отклонено");
  assert.equal(experienceTagLabel("gold", "accepted"), "Gold");
});
