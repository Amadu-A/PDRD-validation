// frontend/tests/manual-review.test.js

/** Интеграция ручных областей Gold и общих контролов review. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewController } from "../src/js/features/review/controller.js";
import { registerVisualizationReview, reviewCalloutBox } from "../src/js/features/analysis/visualization-review.js";

import { FakeElement } from "./helpers/fake-dom.js";

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
  root.append(findings, overview, visualization);

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
    (node) => node.classList.contains("manual-annotation__card"),
  );

  const line = layer.children.find(
    (node) => node.tagName === "SVG",
  );

  assert.equal(region.style.left, "10%");
  assert.equal(card.style.width, "30%");
  assert.equal(card.dataset.manualPage, "22");
  assert.equal(card.dataset.reviewTag, "gold");

  assert.match(
    card.querySelector(".manual-annotation__norm").textContent,
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
    card.querySelector(".manual-annotation__norm").textContent,
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
      (node) => node.classList.contains("manual-annotation__card"),
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
  return ui.layer.children.find((node) => node.classList.contains("manual-annotation__card"));
}

function pageAction(page, hook) {
  return mounted(page).toolbar.children.find((node) => node.dataset[hook] !== undefined);
}

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

function resizeHandle(node, direction) {
  return node.children.find((child) => child.dataset.resizeDirection === direction);
}

function pointer(x, y, pointerId = 7) {
  return { button: 0, pointerId, clientX: 10 + x * 0.8, clientY: 20 + y * 0.5, preventDefault() {}, stopPropagation() {} };
}

test("растягивание границы Gold сохраняется при отпускании; линия, список и Undo синхронны", () => {
  const { root, pages, findings } = fixture();
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  assert.equal(control(card, "geometry"), undefined);
  assert.equal(control(card, "remove"), undefined);
  control(card, "accept").click();
  const before = controller.getManualSnapshot()[0];
  const layer = mounted(pages[0]).layer;
  const issue = layer.children.find((node) => node.className === "manual-annotation__issue");
  const line = layer.children.find((node) => node.tagName === "SVG");
  const oldPoints = line.children[0].getAttribute("points");
  const handle = resizeHandle(issue, "e");
  handle.dispatch("pointerdown", pointer(300, 300));
  handle.dispatch("pointermove", pointer(450, 300));
  assert.deepEqual(controller.getManualSnapshot()[0], before);
  assert.equal(issue.style.width, "35%");
  handle.dispatch("pointerup", pointer(450, 300));
  const after = controller.getManualSnapshot()[0];
  assert.equal(after.issue_box.x_max, 450);
  assert.equal(after.decision, "pending");
  assert.equal(after.experience_tag, "gold");
  assert.equal(after.revision, before.revision + 1);
  assert.notEqual(line.children[0].getAttribute("points"), oldPoints);
  const textCard = findings.children.find((node) => node.dataset.manualFindingId);
  assert.equal(textCard.dataset.reviewDecision, "pending");
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot()[0].issue_box, before.issue_box);
  assert.equal(line.children[0].getAttribute("points"), oldPoints);

  const corner = resizeHandle(card, "se");
  corner.dispatch("pointerdown", pointer(800, 400));
  corner.dispatch("pointerup", pointer(1100, 1200));
  assert.equal(card.style.width, "50%");
  assert.equal(card.style.height, "85%");
  assert.deepEqual(controller.getManualSnapshot()[0].issue_box, before.issue_box);
  assert.equal(controller.getManualSnapshot()[0].callout_box.x_max, 1000);
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getManualSnapshot()[0].callout_box, before.callout_box);
});

test("Esc, pointercancel и новый лист отменяют растягивание без изменения принятия", () => {
  const { root, pages } = fixture([22, 23]);
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  control(card, "accept").click();
  const before = controller.getManualSnapshot();
  const handle = resizeHandle(card, "w");
  for (const cancel of [
    () => pages[0].page.dispatch("keydown", { key: "Escape", preventDefault() {} }),
    () => handle.dispatch("pointercancel", { pointerId: 7 }),
    () => mounted(pages[1]).add.click(),
  ]) {
    handle.dispatch("pointerdown", pointer(500, 200));
    handle.dispatch("pointermove", pointer(400, 200));
    assert.equal(handle.hasPointerCapture(7), true);
    cancel();
    assert.equal(handle.hasPointerCapture(7), false);
    handle.dispatch("pointerup", pointer(400, 200));
    assert.deepEqual(controller.getManualSnapshot(), before);
    assert.equal(card.style.left, "50%");
  }
});

test("красный крест скрывает Gold, сохраняя rejected и текст; Undo возвращает принятое", () => {
  const { root, pages, findings, visualization } = fixture();
  const controller = createReviewController();
  controller.mount(root);
  const card = addGold(root, pages[0]);
  control(card, "edit").click();
  saveDialog(root, "Полный уточнённый текст", "СП 123, п. 5");
  control(card, "accept").click();
  const before = controller.getManualSnapshot()[0];
  const textCard = findings.children.find((node) => node.dataset.manualFindingId);
  control(textCard, "reject").click();
  const rejected = controller.getManualSnapshot()[0];
  assert.equal(rejected.decision, "rejected");
  assert.equal(rejected.experience_tag, "gold");
  assert.equal(rejected.text, before.text);
  assert.deepEqual(rejected.issue_box, before.issue_box);
  for (const node of mounted(pages[0]).layer.children.slice(2)) assert.equal(node.classList.contains("is-hidden"), true);
  assert.equal(textCard.classList.contains("is-hidden"), true);
  assert.match(visualization.children[0].textContent, /1 отклонено/);
  assert.match(findings.querySelector(".manual-review-list__summary").textContent, /0 замечаний/);
  pageAction(pages[0], "reviewPageUndo").click();
  const restored = controller.getManualSnapshot()[0];
  assert.equal(restored.decision, "accepted");
  assert.ok(restored.revision > rejected.revision);
  assert.equal(card.classList.contains("is-hidden"), false);
  assert.equal(textCard.classList.contains("is-hidden"), false);
  assert.match(findings.querySelector(".manual-review-list__summary").textContent, /1 замечаний/);
});

/** Существующая групповая визуализация и текстовый список одного листа. */
function automaticFixture({ unlocated = false } = {}) {
  const result = fixture([22, 23]);
  const { pages, findings, visualization } = result;
  const callout = new FakeElement("article");
  callout.className = "analysis-result__annotation analysis-result__annotation--group";
  callout.rectangle = { left: 450, top: 50, width: 240, height: 200 };
  const records = ["auto:1", "auto:2"].map((findingId, index) => {
    const item = new FakeElement("div");
    item.dataset.findingId = findingId;
    item.dataset.reviewPage = "22";
    const text = new FakeElement("span");
    text.className = "analysis-result__group-member-text";
    text.textContent = `Полное замечание ${index + 1}`;
    item.append(text);
    callout.append(item);
    const bbox = new FakeElement();
    bbox.className = "analysis-result__bbox";
    const connector = new FakeElement("polyline");
    pages[0].pane.append(bbox, connector);
    const article = new FakeElement("article");
    article.className = "analysis-result__finding";
    article.dataset.findingId = findingId;
    article.dataset.reviewPage = "22";
    article.dataset.reviewBasis = "СП 123";
    const fullText = new FakeElement("p");
    fullText.dataset.reviewText = "";
    fullText.textContent = text.textContent;
    article.append(fullText);
    findings.append(article);
    return { findingId, pageNumber: 22, item, callout,
      bboxEntries: unlocated ? [] : [{ node: bbox, box: { xMin: 100 + index * 250, yMin: 100, xMax: 250 + index * 250, yMax: 300 } }],
      connectorEntries: unlocated ? [] : [{ bboxNode: bbox, polyline: connector }],
    };
  });
  pages[0].pane.append(callout);
  visualization.lists.set("[data-finding-id]", records.map((record) => record.item));
  let redraws = 0;
  registerVisualizationReview(pages[0].page, { imagePane: pages[0].pane, image: pages[0].image, records, redraw: () => { redraws += 1; } });
  return { ...result, records, callout, redraws: () => redraws };
}

test("отклонение VLM скрывает только её рамку, линию, подпункт и полный текст; Undo возвращает", () => {
  const { root, pages, findings, records, callout } = automaticFixture();
  const controller = createReviewController();
  controller.mount(root);
  const first = records[0];
  const article = findings.children.find((node) => node.dataset.findingId === first.findingId);
  assert.match(article.querySelector("[data-review-status]").textContent, /ожидает/);
  control(article, "accept").click();
  assert.match(first.item.querySelector("[data-review-status]").textContent, /принято/);
  control(first.item, "reject").click();
  assert.equal(article.classList.contains("is-hidden"), true);
  assert.equal(first.bboxEntries[0].node.classList.contains("is-hidden"), true);
  assert.equal(first.connectorEntries[0].polyline.classList.contains("is-hidden"), true);
  assert.equal(callout.classList.contains("is-hidden"), false);
  const entry = controller.getReviewSnapshot().find((record) => record.findingId === first.findingId);
  assert.equal(entry.experienceTag, "bad");
  assert.equal(entry.originalText, "Полное замечание 1");
  assert.equal(entry.normativeSection, "СП 123");
  assert.equal(entry.visualizations[0].proposed_issue_boxes.length, 1);
  assert.equal(pageAction(pages[1], "reviewPageUndo").disabled, true);
  control(records[1].item, "reject").click();
  assert.equal(callout.classList.contains("is-hidden"), true);
  pageAction(pages[0], "reviewPageUndo").click();
  assert.equal(callout.classList.contains("is-hidden"), false);
  assert.equal(records[0].item.classList.contains("is-hidden"), true);
  pageAction(pages[0], "reviewPageUndo").click();
  assert.equal(article.classList.contains("is-hidden"), false);
  assert.equal(first.bboxEntries[0].node.classList.contains("is-hidden"), false);
  assert.equal(first.item.dataset.reviewDecision, "accepted");
});

test("растягивание рамки VLM и общей карточки сохраняет ID, раздел и соседнюю рамку", () => {
  const { root, pages, records, callout, redraws } = automaticFixture();
  const controller = createReviewController();
  controller.mount(root);
  control(records[0].item, "accept").click();
  const before = controller.getReviewSnapshot();
  const handle = resizeHandle(records[0].bboxEntries[0].node, "se");
  handle.dispatch("pointerdown", pointer(250, 300));
  handle.dispatch("pointermove", pointer(300, 450));
  assert.deepEqual(controller.getReviewSnapshot(), before);
  handle.dispatch("pointerup", pointer(300, 450));
  const after = controller.getReviewSnapshot();
  assert.equal(after[0].decision, "pending");
  assert.equal(after[0].visualizations[0].proposed_issue_boxes[0].x_max, 300);
  assert.deepEqual(after[1], before[1]);
  pageAction(pages[0], "reviewPageUndo").click();
  assert.deepEqual(controller.getReviewSnapshot()[0].visualizations, before[0].visualizations);
  const corner = resizeHandle(callout, "se");
  const saved = controller.getReviewSnapshot();
  corner.dispatch("pointerdown", pointer(850, 600));
  corner.dispatch("pointermove", pointer(900, 750));
  assert.deepEqual(controller.getReviewSnapshot(), saved);
  pages[0].page.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(reviewCalloutBox(callout), null);
  assert.deepEqual(controller.getReviewSnapshot(), saved);
  corner.dispatch("pointerdown", pointer(850, 600));
  corner.dispatch("pointerup", pointer(900, 750));
  assert.ok(reviewCalloutBox(callout));
  assert.equal(controller.getReviewSnapshot()[1].decision, "pending");
  assert.equal(controller.getReviewSnapshot()[1].normativeSection, "СП 123");
  pageAction(pages[0], "reviewPageUndo").click();
  assert.equal(reviewCalloutBox(callout), null);
  assert.ok(redraws() > 0);
});

test("исправленная VLM при отклонении остаётся edited; оба текста доступны после Undo", () => {
  const { root, records, pages } = automaticFixture();
  const controller = createReviewController();
  controller.mount(root);
  control(records[0].item, "edit").click();
  const dialog = root.children.find((node) => node.className === "review-editor");
  dialog.children.find((node) => node.tagName === "TEXTAREA").value = "Уточнённая формулировка инженера";
  dialog.children.find((node) => node.className === "review-editor__actions").children[1].click();
  control(records[0].item, "reject").click();
  const record = controller.getReviewSnapshot()[0];
  assert.equal(record.experienceTag, "edited");
  assert.equal(record.decision, "rejected");
  assert.equal(record.originalText, "Полное замечание 1");
  assert.equal(record.text, "Уточнённая формулировка инженера");
  pageAction(pages[0], "reviewPageUndo").click();
  assert.equal(controller.getReviewSnapshot()[0].decision, "pending");
  assert.equal(controller.getReviewSnapshot()[0].experienceTag, "edited");
});

test("VLM без области рассматривается в обоих списках; рамка не придумывается", () => {
  const { root, records } = automaticFixture({ unlocated: true });
  const controller = createReviewController();
  controller.mount(root);
  control(records[0].item, "accept").click();
  const entry = controller.getReviewSnapshot()[0];
  assert.equal(entry.decision, "accepted");
  assert.deepEqual(entry.visualizations[0].proposed_issue_boxes, []);
});
