// frontend/tests/review-rejection.test.js

/** Отмена, обязательная причина и единая команда отказа без подмены происхождения. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { submitRejection } from "./helpers/rejection.js";
import { createRejectionEditor } from "../src/js/features/review/rejection-editor.js";
import { createReviewState } from "../src/js/features/review/state.js";
import { reviewCommands, reviewEntries } from "../src/js/features/review/commands.js";
import { REJECTION_REASONS } from "../src/js/features/review/rejection-reasons.js";

globalThis.document = { createElement: (tag) => new FakeElement(tag) };

function editorFixture() {
  const root = new FakeElement();
  const calls = [];
  const editor = createRejectionEditor((feedback) => calls.push(feedback));
  root.append(editor.dialog);
  editor.open({ reasonCategory: null, comment: "" });
  return { root, editor, calls };
}

test("диалог содержит семь причин; без выбора решение не изменяется", () => {
  const { root, editor, calls } = editorFixture();
  const select = editor.dialog.querySelector("[data-review-rejection-reason]");
  assert.deepEqual(select.children.slice(1).map((option) => option.value), Object.keys(REJECTION_REASONS));
  submitRejection(root, "");
  assert.equal(editor.dialog.open, true);
  assert.equal(calls.length, 0);
  assert.match(editor.dialog.children[0].children.find((node) => node.getAttribute("role") === "alert").textContent, /Выберите причину/);
});

test("Отмена и Escape не меняют решение и не отправляют комментарий", () => {
  for (const escape of [false, true]) {
    const { editor, calls } = editorFixture();
    editor.dialog.querySelector("[data-review-rejection-comment]").value = "Несохранённый текст";
    if (escape) editor.dialog.dispatch("cancel", { preventDefault() {} });
    else editor.dialog.querySelector("[data-review-rejection-cancel]").click();
    assert.equal(editor.dialog.open, false);
    assert.equal(calls.length, 0);
  }
});

test("комментарий необязателен; подтверждение передаёт категорию и полный текст", () => {
  for (const comment of ["", "  Это резервный насос, значение корректно  "]) {
    const { root, editor, calls } = editorFixture();
    submitRejection(root, "false_positive", comment);
    assert.deepEqual(calls, [{ reasonCategory: "false_positive", comment: comment.trim() }]);
    assert.equal(editor.dialog.open, false);
  }
});

test("восстановление показывает прежнюю причину; длинный комментарий не сохраняется", () => {
  const { root, editor, calls } = editorFixture();
  editor.open({ reasonCategory: "wrong_location", comment: "Не тот насос" });
  assert.equal(editor.dialog.querySelector("[data-review-rejection-reason]").value, "wrong_location");
  assert.equal(editor.dialog.querySelector("[data-review-rejection-comment]").value, "Не тот насос");
  submitRejection(root, "other", "я".repeat(2001));
  assert.equal(editor.dialog.open, true);
  assert.equal(calls.length, 0);
});

test("причина сохраняется, меняется и сбрасывается одной строгой командой Review", () => {
  const state = createReviewState();
  state.register("vlm:1", "Замечание");
  const original = reviewEntries(state.snapshot(), []);
  state.decide("vlm:1", "rejected", { reasonCategory: "false_positive", comment: "Это резервный насос" });
  const rejected = state.get("vlm:1");
  const first = reviewEntries(state.snapshot(), []);
  assert.deepEqual(reviewCommands(original, first), [{ action: "decide", finding_id: "vlm:1",
    decision: "rejected", reason_category: "false_positive", comment: "Это резервный насос" }]);
  state.decide("vlm:1", "rejected", { reasonCategory: "not_applicable", comment: "Требование неприменимо" });
  assert.equal(reviewCommands(first, reviewEntries(state.snapshot(), []))[0].reason_category, "not_applicable");
  state.restoreDecision("vlm:1", rejected.decision, rejected);
  assert.equal(state.get("vlm:1").comment, "Это резервный насос");
  state.decide("vlm:1", "accepted");
  assert.equal(state.get("vlm:1").reasonCategory, null);
  assert.equal(state.get("vlm:1").comment, "");
  assert.deepEqual(reviewCommands(first, reviewEntries(state.snapshot(), [])), [
    { action: "decide", finding_id: "vlm:1", decision: "accepted" },
  ]);
});

test("старые отклонённые снимки загружаются, но новые отказы требуют категории", () => {
  const state = createReviewState();
  state.hydrate([{ finding_id: "vlm:1", origin: "vlm", original_text: "VLM", text: "VLM",
    original_basis: "", normative_basis: "", decision: "rejected", revision: 1 }]);
  assert.equal(state.get("vlm:1").reasonCategory, null);
  assert.equal(state.get("vlm:1").comment, "");
  const snapshot = reviewEntries(state.snapshot(), []);
  assert.deepEqual(reviewCommands(snapshot, snapshot), []);
  assert.throws(() => state.decide("vlm:1", "rejected"), /Выберите причину/);
});
