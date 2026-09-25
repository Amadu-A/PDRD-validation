// frontend/tests/review-state.test.js

/** Проверяет локальные переходы состояний Human Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewState, REVIEW_DECISIONS } from "../src/js/features/review/state.js";

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
  state.decide("A", REVIEW_DECISIONS.REJECTED);
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