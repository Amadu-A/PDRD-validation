// frontend/tests/manual-review.test.js

/** Интеграция ручных областей Gold и общих контролов review. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewController } from "../src/js/features/review/controller.js";

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.selectors = new Map();
    this.lists = new Map();
    this.className = "";

    this.classList = {
      add: (...names) => {
        this.className += ` ${names.join(" ")}`;
      },
    };

    this.textContent = "";
    this.value = "";
    this.hidden = false;

    this.rectangle = {
      left: 10,
      top: 20,
      width: 800,
      height: 500,
    };

    this.captures = new Set();
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  prepend(...nodes) {
    this.children.unshift(...nodes);
  }

  insertBefore(node, before) {
    const index = this.children.indexOf(before);

    this.children.splice(
      index >= 0 ? index : this.children.length,
      0,
      node,
    );
  }

  remove() {
    this.removed = true;
  }

  addEventListener(name, handler) {
    this.listeners.set(
      name,
      [...(this.listeners.get(name) ?? []), handler],
    );
  }

  removeEventListener(name, handler) {
    this.listeners.set(
      name,
      (this.listeners.get(name) ?? []).filter(
        (item) => item !== handler,
      ),
    );
  }

  dispatch(name, event = {}) {
    for (const handler of this.listeners.get(name) ?? []) {
      handler(event);
    }
  }

  click() {
    this.dispatch("click");
  }

  focus() {
    this.focused = true;
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }

  querySelector(selector) {
    return this.selectors.get(selector) ?? null;
  }

  querySelectorAll(selector) {
    return this.lists.get(selector) ?? [];
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  getBoundingClientRect() {
    return this.rectangle;
  }

  setPointerCapture(id) {
    this.captures.add(id);
  }

  hasPointerCapture(id) {
    return this.captures.has(id);
  }

  releasePointerCapture(id) {
    this.captures.delete(id);
  }
}

globalThis.document = {
  createElement(tag) {
    return new FakeElement(tag);
  },

  createElementNS(_namespace, tag) {
    return new FakeElement(tag);
  },
};

globalThis.ResizeObserver = class {
  observe() {}
  disconnect() {}
};

function fixture(pageNumbers = [22], loaded = true) {
  const root = new FakeElement();
  const visualization = new FakeElement();

  root.selectors.set(
    ".analysis-result__visualization",
    visualization,
  );

  visualization.lists.set(
    "[data-finding-id]",
    [],
  );

  const pages = pageNumbers.map((number) => {
    const page = new FakeElement("section");
    const title = new FakeElement("h4");

    title.textContent = `Лист/страница ${number}`;

    const stage = new FakeElement();
    const pane = new FakeElement();
    const image = new FakeElement("img");

    image.complete = loaded;
    image.naturalWidth = loaded ? 1920 : 0;

    page.children = [title, stage];
    pane.children = [image];

    page.selectors.set(
      ".analysis-result__page-title",
      title,
    );

    page.selectors.set(
      ".analysis-result__page-stage",
      stage,
    );

    page.selectors.set(
      ".analysis-result__page-image-pane",
      pane,
    );

    page.selectors.set(
      ".analysis-result__page-image",
      image,
    );

    visualization.children.push(page);

    return {
      page,
      pane,
      image,
    };
  });

  visualization.lists.set(
    ".analysis-result__page-visualization",
    pages.map((page) => page.page),
  );

  return {
    root,
    visualization,
    pages,
  };
}

function mounted(page) {
  const toolbar = page.page.children.find(
    (item) => item.className === "manual-annotation__toolbar",
  );

  const layer = page.pane.children.find(
    (item) => item.className === "manual-annotation__layer",
  );

  return {
    toolbar,
    layer,
    add: toolbar.children[0],
    cancel: toolbar.children[1],
    message: toolbar.children[2],
  };
}

function draw(layer, x1, y1, x2, y2, pointerId = 1) {
  const event = (x, y) => ({
    button: 0,
    pointerId,
    clientX: 10 + x * 0.8,
    clientY: 20 + y * 0.5,

    preventDefault() {},
  });

  layer.dispatch(
    "pointerdown",
    event(x1, y1),
  );

  layer.dispatch(
    "pointermove",
    event(x2, y2),
  );

  layer.dispatch(
    "pointerup",
    event(x2, y2),
  );
}

function control(card, action) {
  const toolbar = card.children.find(
    (item) => item.dataset.reviewControls !== undefined,
  );

  return toolbar.children.find(
    (item) => item.dataset.reviewAction === action,
  );
}

function saveDialog(root, textValue, normValue) {
  const dialog = root.children.find(
    (child) => child.className === "manual-editor",
  );

  const form = dialog.children[0];

  form.children.find(
    (child) => child.tagName === "TEXTAREA",
  ).value = textValue;

  form.children.find(
    (child) => child.tagName === "INPUT",
  ).value = normValue;

  form.dispatch(
    "submit",
    { preventDefault() {} },
  );

  return dialog;
}

test("на листе без VLM-замечаний создаётся Gold с двумя областями и соединителем", () => {
  const {
    root,
    pages,
    visualization,
  } = fixture();

  createReviewController().mount(root);

  const {
    add,
    layer,
  } = mounted(pages[0]);

  assert.equal(
    visualization.children[0].dataset.reviewPreview,
    "",
  );

  add.click();

  draw(layer, 100, 200, 300, 400);

  assert.equal(
    layer.dataset.manualMode,
    "callout",
  );

  draw(layer, 500, 150, 800, 400);

  assert.equal(
    layer.dataset.manualMode,
    "editing",
  );

  const dialog = saveDialog(
    root,
    "Пользователь обнаружил пропуск VLM",
    "СП 123, раздел 4",
  );

  assert.equal(dialog.open, false);

  const region = layer.children.find(
    (node) => node.className === "manual-annotation__issue",
  );

  const card = layer.children.find(
    (node) => node.className === "manual-annotation__card",
  );

  const line = layer.children.find(
    (node) => node.tagName === "SVG",
  );

  assert.equal(region.style.left, "10%");
  assert.equal(card.style.width, "30%");
  assert.equal(card.dataset.manualPage, "22");
  assert.equal(card.dataset.reviewTag, "gold");

  assert.match(
    card.children.find(
      (node) => node.className === "manual-annotation__norm",
    ).textContent,
    /СП 123/,
  );

  assert.equal(
    line.children[0].attributes.has("points"),
    true,
  );

  control(card, "accept").click();

  assert.equal(
    card.dataset.reviewDecision,
    "accepted",
  );

  assert.equal(
    card.dataset.reviewTag,
    "gold",
  );

  control(card, "edit").click();

  saveDialog(
    root,
    "Исправленная формулировка",
    "СП 123, раздел 5",
  );

  assert.equal(
    card.dataset.reviewDecision,
    "pending",
  );

  assert.equal(
    card.dataset.reviewTag,
    "gold",
  );

  assert.match(
    card.children.find(
      (node) => node.className === "manual-annotation__norm",
    ).textContent,
    /раздел 5/,
  );
});

test("нельзя завершить маленькую область, Escape отменяет незавершённое выделение", () => {
  const {
    root,
    pages,
  } = fixture();

  createReviewController().mount(root);

  const {
    add,
    layer,
    message,
    cancel,
  } = mounted(pages[0]);

  add.click();

  draw(layer, 10, 10, 12, 12);

  assert.equal(
    layer.dataset.manualMode,
    "issue",
  );

  assert.match(
    message.textContent,
    /слишком мала/,
  );

  draw(layer, 100, 100, 300, 300);

  assert.equal(
    layer.dataset.manualMode,
    "callout",
  );

  assert.equal(cancel.hidden, false);

  pages[0].page.dispatch(
    "keydown",
    {
      key: "Escape",
      preventDefault() {},
    },
  );

  assert.equal(
    layer.dataset.manualMode,
    "idle",
  );

  assert.equal(cancel.hidden, true);

  assert.equal(
    layer.children.some(
      (node) => node.className === "manual-annotation__card",
    ),
    false,
  );
});

test("новое выделение на другой странице отменяет предыдущее", () => {
  const {
    root,
    pages,
  } = fixture([22, 23]);

  createReviewController().mount(root);

  const first = mounted(pages[0]);
  const second = mounted(pages[1]);

  first.add.click();
  draw(first.layer, 50, 60, 180, 200);

  assert.equal(
    first.layer.dataset.manualMode,
    "callout",
  );

  second.add.click();

  assert.equal(
    first.layer.dataset.manualMode,
    "idle",
  );

  assert.equal(
    second.layer.dataset.manualMode,
    "issue",
  );
});

test("до загрузки картинки начало выделения запрещено", () => {
  const {
    root,
    pages,
  } = fixture([1], false);

  createReviewController().mount(root);

  const {
    add,
    layer,
    message,
  } = mounted(pages[0]);

  add.click();

  assert.equal(
    layer.dataset.manualMode,
    "idle",
  );

  assert.match(
    message.textContent,
    /Дождитесь загрузки/,
  );
});