// frontend/tests/manual-text-list.test.js

/** DOM-проверки текстовых Gold-карточек внутри действующего списка. */

import assert from "node:assert/strict";
import test from "node:test";
import { createManualTextList } from "../src/js/features/review/manual-list.js";

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = new Map();
    this.className = "";
    this.textContent = "";
  }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  querySelector(selector) {
    if (selector === ".analysis-result__empty") {
      return this.children.find((child) => child.className === "analysis-result__empty") ?? null;
    }
    return null;
  }
  querySelectorAll(selector) {
    if (selector === ".analysis-result__finding") {
      return this.children.filter((child) => child.className === "analysis-result__finding");
    }
    return [];
  }
}

globalThis.document = { createElement: (tag) => new Element(tag) };

function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {
    const result = find(child, predicate);
    if (result) return result;
  }
  return null;
}

function fixture(automaticCount = 2) {
  const section = new Element("section");
  for (let i = 0; i < automaticCount; i += 1) {
    const article = new Element("article");
    article.className = "analysis-result__finding";
    section.append(article);
  }
  if (automaticCount === 0) {
    const empty = new Element("p");
    empty.className = "analysis-result__empty";
    empty.textContent = "Замечания не сформированы.";
    section.append(empty);
  }
  const root = {
    querySelector(selector) {
      return selector === ".analysis-result__findings" ? section : null;
    },
  };
  const note = { findingId: "manual:1", pageNumber: 22 };
  const review = {
    findingId: "manual:1",
    text: "Пользователь обнаружил пропуск",
    normativeSection: "СП 123, п. 4",
    decision: "pending",
  };
  return { section, root, note, review };
}

test("Gold добавляется в общий текстовый список после замечаний VLM", () => {
  const view = createManualTextList();
  const { section, root, note, review } = fixture();
  view.mount(root);
  const created = view.add(note, review);
  assert.equal(created.article.dataset.manualFindingId, "manual:1");
  assert.equal(created.article.dataset.manualPage, "22");
  const gold = find(created.article, (node) => node.textContent === "Gold · пользователь");
  assert.ok(gold);
  assert.match(created.article.className, /manual-review-list__item/);
  assert.equal(created.textNode.textContent, "Пользователь обнаружил пропуск");
  assert.match(
    find(section, (node) => node.className === "manual-review-list__summary").textContent,
    /3 замечаний \(2 VLM, 1 добавлено пользователем\)/,
  );
  assert.match(
    find(created.article, (node) => node.textContent === "3. Лист/страница 22").textContent,
    /22/,
  );
});

test("принятие и изменение Gold синхронизируются без создания дубликатов", () => {
  const view = createManualTextList();
  const { section, root, note, review } = fixture();
  view.mount(root);
  const created = view.add(note, review);
  view.sync({
    ...review,
    text: "Текст исправлен",
    normativeSection: "СП 123, п. 5",
    decision: "accepted",
  });
  assert.equal(created.textNode.textContent, "Текст исправлен");
  assert.equal(created.article.dataset.manualDecision, "accepted");
  assert.ok(find(created.article, (node) => node.textContent === "СП 123, п. 5"));
  assert.ok(find(created.article, (node) => node.textContent === "Принято пользователем"));
  assert.equal(section.children.filter((node) => node === created.article).length, 1);
  assert.throws(() => view.add(note, review));
});

test("страница без находок VLM корректно сообщает о ручном Gold", () => {
  const view = createManualTextList();
  const { section, root, note, review } = fixture(0);
  view.mount(root);
  view.add(note, { ...review, normativeSection: "" });
  assert.match(section.querySelector(".analysis-result__empty").textContent, /Автоматические/);
  assert.ok(find(section, (node) => node.textContent === "Не указано"));
});

test("недоступный раздел и новое отображение не сохраняют старые DOM-ссылки", () => {
  const view = createManualTextList();
  const { root, note, review } = fixture();
  view.mount({ querySelector: () => null });
  assert.equal(view.add(note, review), null);
  view.mount(root);
  assert.ok(view.add(note, review));
  view.mount(root);
  assert.ok(view.add(note, review));
});

test("вредоносный HTML остаётся обычным текстом", () => {
  const view = createManualTextList();
  const { root, note, review } = fixture();
  view.mount(root);
  const created = view.add(note, { ...review, text: "<img src=x onerror=alert(1)>" });
  assert.equal(created.textNode.textContent, "<img src=x onerror=alert(1)>");
  assert.deepEqual(created.textNode.children, []);
});