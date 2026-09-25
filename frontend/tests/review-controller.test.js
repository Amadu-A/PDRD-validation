// frontend/tests/review-controller.test.js

/** DOM smoke/integration-тесты контролов без внешних зависимостей. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewController } from "../src/js/features/review/controller.js";

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.selectors = new Map();
    this.lists = new Map();
    this.textContent = "";
    this.value = "";
  }

  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  remove() { this.removed = true; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name); }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  querySelector(selector) { return this.selectors.get(selector) ?? null; }
  querySelectorAll(selector) { return this.lists.get(selector) ?? []; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { this.listeners.get("click")?.(); }
}

globalThis.document = {
  createElement(tag) { return new FakeElement(tag); },
};

function fixture(findings) {
  const root = new FakeElement();
  const visualization = new FakeElement();
  root.selectors.set(".analysis-result__visualization", visualization);
  const items = findings.map(([id, text]) => {
    const item = new FakeElement();
    item.dataset.findingId = id;
    const label = new FakeElement("span");
    label.textContent = text;
    item.selectors.set(
      ".analysis-result__annotation-title, .analysis-result__group-member-text",
      label,
    );
    return { item, label };
  });
  visualization.lists.set("[data-finding-id]", items.map(({ item }) => item));
  return { root, visualization, items };
}

function action(item, name) {
  const toolbar = item.children.find((child) => child.dataset.reviewControls !== undefined);
  return toolbar.children.find((child) => child.dataset.reviewAction === name);
}

function status(item) {
  const toolbar = item.children.find((child) => child.dataset.reviewControls !== undefined);
  return toolbar.children.find((child) => child.dataset.reviewStatus !== undefined);
}

test("монтирует контролы для обычного и сгруппированного finding", () => {
  const { root, visualization, items } = fixture([
    ["solo", "Обычная карточка"],
    ["group", "Строка групповой карточки"],
  ]);
  createReviewController().mount(root);
  assert.equal(items[0].item.children.length, 1);
  assert.equal(items[1].item.children.length, 1);
  assert.equal(visualization.children[0].dataset.reviewPreview, "");
  action(items[0].item, "accept").click();
  action(items[1].item, "reject").click();
  assert.equal(items[0].item.dataset.reviewDecision, "accepted");
  assert.equal(items[1].item.dataset.reviewDecision, "rejected");
  assert.equal(action(items[0].item, "accept").getAttribute("aria-pressed"), "true");
  assert.match(status(items[1].item).textContent, /Bad/);
  assert.match(visualization.children[0].textContent, /0 без решения/);
});

test("несколько представлений finding синхронизируются после правки", () => {
  const { root, items } = fixture([
    ["same", "Один текст"],
    ["same", "Один текст"],
  ]);
  createReviewController().mount(root);
  action(items[0].item, "accept").click();
  assert.equal(items[1].item.dataset.reviewDecision, "accepted");
  action(items[1].item, "edit").click();
  const dialog = root.children.find((child) => child.tagName === "DIALOG");
  const textarea = dialog.children.find((child) => child.tagName === "TEXTAREA");
  const buttons = dialog.children.find((child) => child.className === "review-editor__actions");
  textarea.value = "Исправленный полный текст";
  buttons.children[1].click();
  assert.equal(dialog.open, false);
  assert.equal(items[0].label.textContent, "Исправленный полный текст");
  assert.equal(items[1].label.textContent, "Исправленный полный текст");
  assert.equal(items[0].item.dataset.reviewDecision, "pending");
  assert.equal(action(items[0].item, "edit").getAttribute("aria-pressed"), "true");
  action(items[0].item, "accept").click();
  assert.equal(items[1].item.dataset.reviewDecision, "accepted");
  assert.equal(items[1].item.dataset.reviewEdited, "true");
});

test("невалидная правка оставляет прежнее решение и показывает ошибку", () => {
  const { root, items } = fixture([["one", "Исходный текст"]]);
  createReviewController().mount(root);
  action(items[0].item, "accept").click();
  action(items[0].item, "edit").click();
  const dialog = root.children.find((child) => child.tagName === "DIALOG");
  dialog.children.find((child) => child.tagName === "TEXTAREA").value = " ";
  dialog.children.find((child) => child.className === "review-editor__actions")
    .children[1].click();
  assert.equal(dialog.open, true);
  assert.equal(items[0].item.dataset.reviewDecision, "accepted");
  assert.match(dialog.children.find((child) => child.getAttribute("role") === "alert")
    .textContent, /не может быть пустым/);
});

test("новый отчёт сбрасывает прошлые локальные решения", () => {
  const controller = createReviewController();
  const previous = fixture([["one", "Первое"]]);
  controller.mount(previous.root);
  action(previous.items[0].item, "accept").click();
  const next = fixture([["one", "Другое"]]);
  controller.mount(next.root);
  assert.equal(next.items[0].item.dataset.reviewDecision, "pending");
  assert.equal(next.items[0].label.textContent, "Другое");
});

test("при отсутствии визуальных замечаний не добавляет фиктивный review", () => {
  const { root } = fixture([]);
  createReviewController().mount(root);
  assert.equal(root.children.length, 0);
});