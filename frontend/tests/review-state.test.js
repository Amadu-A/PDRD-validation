// frontend/tests/review-state.test.js

/** Проверяет локальные переходы состояний Human Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewState, REVIEW_DECISIONS } from "../src/js/features/review/state.js";

test("изменение только области и принятие сохраняют Wise, текст — Edited, ручное — Gold", () => {
  const state = createReviewState();
  for (const [id, origin] of [["wise", "vlm"], ["edited", "vlm"], ["gold", "manual"]]) {
    state.register(id, "Исходный текст", { origin });
    state.decide(id, REVIEW_DECISIONS.ACCEPTED);
    if (id === "edited") state.edit(id, "Исправленный текст");
    state.invalidate(id);
    assert.equal(state.get(id).decision, REVIEW_DECISIONS.PENDING);
    state.decide(id, REVIEW_DECISIONS.ACCEPTED);
    assert.equal(state.get(id).experienceTag, id);
  }
});

test("регистрация и решения независимы между findings", () => {
  const state = createReviewState();
  state.register("A", "Первое замечание");
  state.register("B", "Второе замечание");
  state.decide("A", REVIEW_DECISIONS.ACCEPTED);
  assert.deepEqual(state.summary(), {
    total: 2, pending: 1, accepted: 1, rejected: 0,
  });
  assert.equal(state.get("B").decision, REVIEW_DECISIONS.PENDING);
});

test("редактирование принятого замечания требует повторного решения", () => {
  const state = createReviewState();
  state.register("A", "Было");
  state.decide("A", REVIEW_DECISIONS.ACCEPTED);
  const changed = state.edit("A", "  Стало  ");
  assert.equal(changed.text, "Стало");
  assert.equal(changed.edited, true);
  assert.equal(changed.decision, REVIEW_DECISIONS.PENDING);
  state.decide("A", REVIEW_DECISIONS.REJECTED, { reasonCategory: "false_positive" });
  assert.equal(state.get("A").edited, true);
  assert.equal(state.get("A").revision, 3);
});

test("повторное действие не изменяет версию и не теряет исходник", () => {
  const state = createReviewState();
  state.register("A", "Оригинал");
  state.register("A", "Другая видимая копия");
  state.decide("A", REVIEW_DECISIONS.ACCEPTED);
  state.decide("A", REVIEW_DECISIONS.ACCEPTED);
  state.edit("A", "Оригинал");
  assert.equal(state.get("A").revision, 1);
  assert.equal(state.get("A").originalText, "Оригинал");
  assert.equal(state.get("A").decision, REVIEW_DECISIONS.ACCEPTED);
});

test("возврат к исходному тексту снимает edited и сбрасывает решение", () => {
  const state = createReviewState();
  state.register("A", "Оригинал");
  state.edit("A", "Новый текст");
  state.decide("A", REVIEW_DECISIONS.ACCEPTED);
  const entry = state.edit("A", "Оригинал");
  assert.equal(entry.edited, false);
  assert.equal(entry.decision, REVIEW_DECISIONS.PENDING);
});

test("валидация запрещает пустые данные, неизвестные ID и неверные решения", () => {
  const state = createReviewState();
  assert.throws(() => state.register("", "Текст"));
  assert.throws(() => state.register("A", "  "));
  state.register("A", "Исходный текст");
  assert.throws(() => state.get("missing"));
  assert.throws(() => state.decide("A", "pending"));
  assert.throws(() => state.edit("A", " "));
  assert.throws(() => state.edit("A", "x".repeat(10001)));
  assert.equal(state.get("A").revision, 0);
});

test("отмена создания удаляет только локальное Gold; восстановление сохраняет полный снимок", () => {
  const state = createReviewState();
  state.register("vlm", "Замечание VLM");
  state.register("manual:one", "Замечание инженера", { origin: "manual", normativeSection: "СП 123" });
  state.decide("manual:one", "accepted");
  const pending = state.invalidateManual("manual:one");
  assert.equal(pending.decision, "pending");
  assert.equal(pending.experienceTag, "gold");
  assert.equal(pending.revision, 2);
  state.decide("manual:one", "rejected", { reasonCategory: "false_positive" });
  const snapshot = state.removeManual("manual:one");
  assert.equal(state.summary().total, 1);
  assert.throws(() => state.get("manual:one"));
  assert.deepEqual(state.restoreManual(snapshot), snapshot);
  snapshot.text = "Внешнее изменение";
  assert.equal(state.get("manual:one").text, "Замечание инженера");
  assert.throws(() => state.restoreManual(snapshot));
  assert.throws(() => state.removeManual("vlm"));
  assert.throws(() => state.invalidateManual("vlm"));
  assert.equal(state.summary().total, 2);
});

test("Undo отклонения сохраняет edited, исходный раздел и обе формулировки", () => {
  const state = createReviewState();
  state.register("A", "Исходная формулировка", { normativeSection: "СП 123" });
  state.edit("A", "Исправленная формулировка");
  state.decide("A", "accepted");
  const previous = state.get("A");
  state.decide("A", "rejected", { reasonCategory: "false_positive" });
  assert.equal(state.snapshot()[0].experienceTag, "edited");
  const undone = state.restoreDecision("A", previous.decision);
  assert.equal(undone.experienceTag, "edited");
  assert.equal(undone.text, "Исправленная формулировка");
  assert.equal(undone.originalText, "Исходная формулировка");
  assert.equal(undone.normativeSection, "СП 123");
  assert.equal(undone.originalNormativeSection, "СП 123");
  assert.equal(undone.revision, previous.revision + 2);
  state.invalidate("A");
  assert.equal(state.get("A").decision, "pending");
  assert.equal(state.get("A").experienceTag, "edited");
  state.edit("A", "Исходная формулировка");
  assert.equal(state.get("A").edited, false);
  const copy = state.snapshot();
  copy[0].text = "Внешняя мутация";
  assert.equal(state.get("A").text, "Исходная формулировка");
  assert.throws(() => state.restoreDecision("A", "invalid"));
});
