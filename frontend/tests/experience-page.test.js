// frontend/tests/experience-page.test.js

/** Проверяет фильтрацию, правку и открытие миниатюры в браузерном контроллере. */
import assert from "node:assert/strict";
import test from "node:test";

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.textContent = "";
    this.value = "";
    this.checked = false;
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  click() { this.listeners.get("click")?.(); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.focused = true; }
  emit(name) { this.listeners.get(name)?.({ preventDefault() {} }); }
}

test("страница показывает демо, фильтрует, редактирует и раскрывает область", async () => {
  const elements = new Map();
  const keys = [
    "[data-experience-filter]", "[data-experience-rows]",
    "[data-experience-count]", "[data-experience-image-dialog]",
    "[data-experience-large-image]", "[data-experience-edit-dialog]",
    "[data-experience-edit-form]", "[data-experience-edit-error]",
    "[data-experience-image-close]", "[data-experience-edit-close]",
  ];
  for (const key of keys) { elements.set(key, new FakeElement()); }
  const fields = new Map([
    ["query", new FakeElement("input")],
    ["tag", new FakeElement("select")],
    ["decision", new FakeElement("select")],
    ["active", new FakeElement("input")],
    ["text", new FakeElement("textarea")],
    ["normative_basis", new FakeElement("textarea")],
  ]);
  elements.get("[data-experience-edit-form]").elements = {
    namedItem: (name) => fields.get(name),
  };
  globalThis.document = {
    querySelector: (key) => elements.get(key),
    createElement: (tag) => new FakeElement(tag),
  };
  globalThis.FormData = class {
    constructor() { this.values = ["query", "tag", "decision", "active"]
      .map((key) => [key, fields.get(key).value]); }
    entries() { return this.values; }
  };

  await import("../src/js/features/experience/page.js");
  const rows = elements.get("[data-experience-rows]");
  assert.equal(rows.children.length, 2);

  fields.get("tag").value = "edited";
  elements.get("[data-experience-filter]").emit("change");
  assert.equal(rows.children.length, 1);
  assert.equal(rows.children[0].dataset.experienceId, "demo-edited");

  const recordRow = rows.children[0];
  recordRow.children[0].children[0].click();
  assert.equal(elements.get("[data-experience-image-dialog]").open, true);
  assert.match(elements.get("[data-experience-large-image]").src,
    /^data:image\/svg\+xml,/);
  elements.get("[data-experience-image-close]").click();
  assert.equal(elements.get("[data-experience-image-dialog]").open, false);

  recordRow.children[5].children[0].click();
  assert.equal(elements.get("[data-experience-edit-dialog]").open, true);
  fields.get("text").value = "Уточнённый пример";
  fields.get("active").checked = true;
  elements.get("[data-experience-edit-form]").emit("submit");
  assert.equal(elements.get("[data-experience-edit-dialog]").open, false);
  assert.equal(rows.children[0].children[2].children[0].textContent,
    "Уточнённый пример");
  assert.equal(rows.children[0].children[4].textContent, "Активно");
});
