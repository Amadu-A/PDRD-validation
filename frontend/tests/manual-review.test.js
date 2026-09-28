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
    for (const node of nodes) {
      node.remove();
      node.parent = this;
      node.removed = false;
      this.children.push(node);
    }
  }

  prepend(...nodes) {
    for (const node of [...nodes].reverse()) {
      node.remove();
      node.parent = this;
      node.removed = false;
      this.children.unshift(node);
    }
  }

  insertBefore(node, before) {
    const index = this.children.indexOf(before);

    this.children.splice(
      index >= 0 ? index : this.children.length,
      0,
      node,
    );
    node.parent = this;
  }

  remove() {
    this.removed = true;
    if (this.parent) {
      this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
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
  const findings = new FakeElement("section");
  const overview = new FakeElement("section");
  const empty = new FakeElement("p");
  empty.className = "analysis-result__empty";
  empty.textContent = "Замечания не сформированы.";
  findings.append(empty);
  findings.selectors.set(".analysis-result__empty", empty);
  root.selectors.set(".analysis-result__findings", findings);
  root.selectors.set(".analysis-result__summary", overview);

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
    const pageEmpty = new FakeElement("p");
    pageEmpty.textContent = "На листе замечаний нет.";
    page.selectors.set(".analysis-result__page-empty", pageEmpty);

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
  root.lists.set(".analysis-result__page-visualization", pages.map((item) => item.page));

  return {
    root,
    visualization,
    pages,
    findings,
    overview,
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
    (child) => child.className === "manual-editor" && child.open,
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

/** Создаёт Gold через реальные обработчики указателя и формы. */
function addGold(root, page, text = "Gold для проверки", norm = "СП 123, п. 4") {
  const ui = mounted(page);
  ui.add.click();
  draw(ui.layer, 100, 200, 300, 400);
  draw(ui.layer, 500, 150, 800, 400);
  saveDialog(root, text, norm);
  return ui.layer.children.find((node) => node.className === "manual-annotation__card");
}

function pageAction(page, hook) {
  return mounted(page).toolbar.children.find((node) => node.dataset[hook] !== undefined);
}

test("геометрия Gold меняется только после подтверждения и сбрасывает решение в обоих видах", () => {
  const { root, pages, findings } = fixture();
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  control(card, "accept").click();
  const before = controller.getManualSnapshot();
  const line = mounted(pages[0]).layer.children.find((node) => node.tagName === "SVG");
  const oldPoints = line.children[0].getAttribute("points");
  control(card, "geometry").click();
  draw(mounted(pages[0]).layer, 10, 10, 12, 12);
  assert.deepEqual(controller.getManualSnapshot(), before);
  draw(mounted(pages[0]).layer, 200, 300, 450, 500);
  draw(mounted(pages[0]).layer, 600, 500, 900, 850);
  assert.equal(mounted(pages[0]).layer.dataset.manualMode, "confirm");
  assert.deepEqual(controller.getManualSnapshot(), before);
  pageAction(pages[0], "reviewGeometrySave").click();
  const after = controller.getManualSnapshot();
  assert.equal(after.length, 1);
  assert.equal(after[0].finding_id, before[0].finding_id);
  assert.equal(after[0].page_number, 22);
  assert.equal(after[0].experience_tag, "gold");
  assert.equal(after[0].decision, "pending");
  assert.equal(after[0].revision, before[0].revision + 1);
  assert.equal(after[0].issue_box.x_min, 200);
  assert.equal(card.style.left, "60%");
  assert.notEqual(line.children[0].getAttribute("points"), oldPoints);
  const textCard = findings.children.find((node) => node.dataset.manualFindingId);
  assert.equal(textCard.dataset.reviewDecision, "pending");
  control(textCard, "accept").click();
  assert.equal(card.dataset.reviewDecision, "accepted");
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot()[0].issue_box, before[0].issue_box);
  assert.deepEqual(controller.getManualSnapshot()[0].callout_box, before[0].callout_box);
  assert.equal(controller.getManualSnapshot()[0].decision, "pending");
  assert.equal(line.children[0].getAttribute("points"), oldPoints);
});

test("Escape и смена листа отменяют черновик существующей геометрии без потери принятия", () => {
  const { root, pages } = fixture([22, 23]);
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  control(card, "accept").click();
  const before = controller.getManualSnapshot();
  control(card, "geometry").click();
  draw(mounted(pages[0]).layer, 200, 200, 400, 400);
  draw(mounted(pages[0]).layer, 600, 600, 900, 850);
  pages[0].page.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.deepEqual(controller.getManualSnapshot(), before);
  assert.equal(mounted(pages[0]).layer.dataset.manualMode, "idle");
  assert.equal(pageAction(pages[0], "reviewGeometrySave").hidden, true);
  control(card, "geometry").click();
  draw(mounted(pages[0]).layer, 200, 200, 400, 400);
  mounted(pages[1]).add.click();
  assert.equal(mounted(pages[0]).layer.dataset.manualMode, "idle");
  assert.equal(mounted(pages[1]).layer.dataset.manualMode, "issue");
  assert.deepEqual(controller.getManualSnapshot(), before);
});

test("удаление из списка и Undo синхронизируют рамку, линию, карточки, счётчики и снимок", () => {
  const { root, pages, findings, overview, visualization } = fixture();
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  control(card, "edit").click();
  saveDialog(root, "Полный уточнённый текст", "СП 123, п. 5");
  control(card, "accept").click();
  const before = controller.getManualSnapshot();
  const textCard = findings.children.find((node) => node.dataset.manualFindingId);
  control(textCard, "remove").click();
  assert.deepEqual(controller.getManualSnapshot(), []);
  assert.equal(card.removed, true);
  assert.equal(textCard.removed, true);
  assert.equal(mounted(pages[0]).layer.children.length, 2);
  assert.equal(findings.children.length, 1);
  assert.equal(overview.children.length, 0);
  assert.equal(findings.children[0].textContent, "Замечания не сформированы.");
  assert.equal(pages[0].page.querySelector(".analysis-result__page-empty").textContent, "На листе замечаний нет.");
  assert.match(visualization.children[0].textContent, /0 без решения, 0 принято/);
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot(), before);
  assert.equal(card.removed, false);
  assert.equal(card.children.filter((node) => node.dataset.reviewControls !== undefined).length, 1);
  assert.equal(findings.children.filter((node) => node.dataset.manualFindingId).length, 1);
  const restored = findings.children.find((node) => node.dataset.manualFindingId);
  control(restored, "reject").click();
  assert.equal(card.dataset.reviewDecision, "rejected");
  assert.equal(card.dataset.reviewTag, "gold");
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot(), []);
  assert.equal(mounted(pages[0]).layer.children.length, 2);
  assert.equal(pageAction(pages[0], "reviewPageUndo").disabled, true);
});

test("Undo добавления действует на свой лист и не удаляет соседнее замечание", () => {
  const { root, pages, findings } = fixture([22, 23]);
  const controller = createReviewController();
  controller.mount(root);
  addGold(root, pages[0], "Первый лист");
  const second = addGold(root, pages[1], "Второй лист");
  pageAction(pages[0], "reviewPageUndo").click();
  const data = controller.getManualSnapshot();
  assert.equal(data.length, 1);
  assert.equal(data[0].page_number, 23);
  assert.equal(data[0].text, "Второй лист");
  assert.equal(second.removed, false);
  const remaining = findings.children.find((node) => node.dataset.manualFindingId);
  assert.equal(remaining.children[1].children[0].textContent, "1. Лист/страница 23");
});

test("смена отчёта очищает историю и отключает старые обработчики указателя и Undo", () => {
  const controller = createReviewController();
  const previous = fixture([22]);
  controller.mount(previous.root);
  const previousCard = addGold(previous.root, previous.pages[0]);
  const oldUndo = pageAction(previous.pages[0], "reviewPageUndo");
  const next = fixture([23]);
  controller.mount(next.root);
  assert.equal(pageAction(next.pages[0], "reviewPageUndo").disabled, true);
  oldUndo.click();
  mounted(previous.pages[0]).add.click();
  draw(mounted(previous.pages[0]).layer, 100, 100, 400, 400);
  assert.equal(mounted(previous.pages[0]).layer.dataset.manualMode, "idle");
  assert.equal(previousCard.removed, false);
  assert.deepEqual(controller.getManualSnapshot(), []);
  addGold(next.root, next.pages[0]);
  pageAction(next.pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot(), []);
});

test("отмена выделения освобождает захват указателя и сохраняет прежние области", () => {
  const { root, pages } = fixture();
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  const before = controller.getManualSnapshot();
  control(card, "geometry").click();
  const { layer, cancel } = mounted(pages[0]);
  layer.dispatch("pointerdown", {
    button: 0, pointerId: 5, clientX: 110, clientY: 150, preventDefault() {},
  });
  assert.equal(layer.hasPointerCapture(5), true);
  cancel.click();
  assert.equal(layer.hasPointerCapture(5), false);
  layer.dispatch("pointerup", { pointerId: 5 });
  assert.deepEqual(controller.getManualSnapshot(), before);
});
