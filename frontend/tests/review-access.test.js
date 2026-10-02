// frontend/tests/review-access.test.js

/** Регрессия: проектировщик не может решить или править VLM-замечание из UI. */
import assert from "node:assert/strict";
import test from "node:test";
import { createReviewController } from "../src/js/features/review/controller.js";
import { FakeElement } from "./helpers/fake-dom.js";

globalThis.document = { createElement: (tag) => new FakeElement(tag) };

test("без review.findings.decide кнопки решения не действуют", () => {
  const root = new FakeElement();
  const visualization = new FakeElement();
  root.selectors.set(".analysis-result__visualization", visualization);
  const item = new FakeElement();
  item.dataset.findingId = "f1";
  const text = new FakeElement("span");
  text.textContent = "Автоматическое замечание";
  item.selectors.set(
    ".analysis-result__annotation-title, .analysis-result__group-member-text", text,
  );
  visualization.lists.set("[data-finding-id]", [item]);
  createReviewController({ capabilities: {
    canCreateGold: true, canDecide: false,
  } }).mount(root);
  const controls = item.children.find((child) => child.dataset.reviewControls !== undefined);
  const accept = controls.children.find((child) => child.dataset.reviewAction === "accept");
  const reject = controls.children.find((child) => child.dataset.reviewAction === "reject");
  const edit = controls.children.find((child) => child.dataset.reviewAction === "edit");
  assert.equal(accept.hidden, true);
  assert.equal(reject.hidden, true);
  assert.equal(edit.hidden, true);
  accept.click();
  reject.click();
  assert.equal(item.dataset.reviewDecision, "pending");
});
