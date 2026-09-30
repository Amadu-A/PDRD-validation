// frontend/tests/review-tag.test.js

/** Разделение меток происхождения и решения для Experience DB. */

import assert from "node:assert/strict";
import test from "node:test";

import {
  createReviewState,
  REVIEW_DECISIONS,
  REVIEW_ORIGINS,
} from "../src/js/features/review/state.js";

test("неизменённый вывод VLM имеет wise или bad после решения", () => {
  const model = createReviewState();

  model.register("f1", "Вывод VLM");

  assert.equal(
    model.get("f1").experienceTag,
    null,
  );

  assert.equal(
    model.decide("f1", REVIEW_DECISIONS.ACCEPTED).experienceTag,
    "wise",
  );

  assert.equal(
    model.decide("f1", REVIEW_DECISIONS.REJECTED).experienceTag,
    "bad",
  );
});

test("исправленный пользователем вывод VLM всегда edited, даже после принятия", () => {
  const model = createReviewState();

  model.register("f1", "Вывод VLM");
  model.decide("f1", REVIEW_DECISIONS.ACCEPTED);

  const entry = model.edit(
    "f1",
    "Исправлено",
  );

  assert.equal(entry.experienceTag, "edited");
  assert.equal(entry.decision, REVIEW_DECISIONS.PENDING);

  assert.equal(
    model.decide("f1", REVIEW_DECISIONS.REJECTED).experienceTag,
    "edited",
  );

  assert.equal(
    model.decide("f1", REVIEW_DECISIONS.ACCEPTED).experienceTag,
    "edited",
  );
});

test("ручная находка остаётся gold после редактирования, принятия и отклонения", () => {
  const model = createReviewState();

  model.register(
    "manual:x",
    "Пропуск VLM",
    {
      origin: REVIEW_ORIGINS.MANUAL,
      normativeSection: "СП 1",
    },
  );

  assert.equal(
    model.get("manual:x").experienceTag,
    "gold",
  );

  model.decide(
    "manual:x",
    REVIEW_DECISIONS.ACCEPTED,
  );

  const edited = model.edit(
    "manual:x",
    "Уточнено",
    { normativeSection: "СП 2" },
  );

  assert.equal(edited.experienceTag, "gold");
  assert.equal(edited.decision, REVIEW_DECISIONS.PENDING);
  assert.equal(edited.normativeSection, "СП 2");

  assert.equal(
    model.decide("manual:x", REVIEW_DECISIONS.REJECTED).experienceTag,
    "gold",
  );
});

test("повторная регистрация не меняет происхождение, конфликт блокируется", () => {
  const model = createReviewState();

  model.register("x", "VLM");
  model.register("x", "VLM");

  assert.throws(
    () => model.register(
      "x",
      "manual",
      { origin: REVIEW_ORIGINS.MANUAL },
    ),
  );

  assert.throws(
    () => model.register("", "text"),
  );

  assert.throws(
    () => model.register(
      "b",
      "test",
      { origin: "unknown" },
    ),
  );

  assert.throws(
    () => model.register(
      "manual:y",
      "x",
      {
        origin: REVIEW_ORIGINS.MANUAL,
        normativeSection: "x".repeat(2001),
      },
    ),
  );
});