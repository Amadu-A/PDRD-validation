// frontend/tests/review-resize.test.js

/** Unit и функциональные проверки границ, углов и жизненного цикла растягивания. */

import assert from "node:assert/strict";
import test from "node:test";
import { resizedBox, RESIZE_DIRECTIONS } from "../src/js/features/review/resize-geometry.js";
import { attachBoxResize } from "../src/js/features/review/resize.js";
import { FakeElement } from "./helpers/fake-dom.js";

const BOX = { x_min: 100, y_min: 200, x_max: 500, y_max: 600 };

test("перемещение сохраняет размер и ограничено листом", () => {
  assert.deepEqual(resizedBox(BOX, "move", { x: 25, y: -30 }), { x_min: 125, y_min: 170, x_max: 525, y_max: 570 });
  assert.deepEqual(resizedBox(BOX, "move", { x: -2000, y: 2000 }), { x_min: 0, y_min: 600, x_max: 400, y_max: 1000 });
});

test("жест перемещения карточки сохраняется целиком, Escape откатывает без commit", () => {
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const node = new FakeElement(); const page = new FakeElement();
  let shown = BOX; let saved = BOX; let commits = 0;
  const resize = attachBoxResize({ node, page, label: "Карточка", movable: true,
    bounds: () => ({ width: 1000, height: 1000 }), getBox: () => ({ ...saved }),
    preview: (value) => { shown = value; }, commit: (value) => { saved = value; commits += 1; } });
  const handle = node.children.find((child) => child.dataset.resizeDirection === "move");
  const point = { button: 0, pointerId: 1, clientX: 100, clientY: 200, preventDefault() {}, stopPropagation() {} };
  handle.dispatch("pointerdown", point);
  handle.dispatch("pointermove", { ...point, clientX: 125, clientY: 230 });
  page.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.deepEqual(shown, BOX); assert.equal(commits, 0);
  handle.dispatch("pointerdown", point);
  handle.dispatch("pointerup", { ...point, clientX: 125, clientY: 230 });
  assert.deepEqual(saved, { x_min: 125, y_min: 230, x_max: 525, y_max: 630 });
  assert.equal(commits, 1); resize.dispose();
});

test("все восемь границ двигают только выбранные стороны", () => {
  for (const direction of RESIZE_DIRECTIONS) {
    const result = resizedBox(BOX, direction, { x: 30, y: -40 });
    assert.equal(result.x_min, direction.includes("w") ? 130 : 100);
    assert.equal(result.x_max, direction.includes("e") ? 530 : 500);
    assert.equal(result.y_min, direction.includes("n") ? 160 : 200);
    assert.equal(result.y_max, direction.includes("s") ? 560 : 600);
  }
  assert.deepEqual(BOX, { x_min: 100, y_min: 200, x_max: 500, y_max: 600 });
  assert.throws(() => resizedBox(BOX, "invalid", { x: 0, y: 0 }));
});

test("границы листа и минимум защищают от выворачивания прямоугольника", () => {
  assert.deepEqual(resizedBox(BOX, "nw", { x: -2000, y: -2000 }), { ...BOX, x_min: 0, y_min: 0 });
  assert.deepEqual(resizedBox(BOX, "se", { x: 2000, y: 2000 }), { ...BOX, x_max: 1000, y_max: 1000 });
  const minimum = { minWidth: 130, minHeight: 80 };
  assert.deepEqual(resizedBox(BOX, "nw", { x: 2000, y: 2000 }, minimum), { ...BOX, x_min: 370, y_min: 520 });
  assert.deepEqual(resizedBox(BOX, "se", { x: -2000, y: -2000 }, minimum), { ...BOX, x_max: 230, y_max: 280 });
  const small = { x_min: 100, y_min: 100, x_max: 110, y_max: 110 };
  assert.deepEqual(resizedBox(small, "nw", { x: 0, y: 0 }), small);
});

test("клавиатура сохраняет изменение, чужой указатель игнорируется, dispose отменяет жест", () => {
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const node = new FakeElement();
  const page = new FakeElement();
  let box = { ...BOX };
  let shown = { ...BOX };
  let commits = 0;
  const resize = attachBoxResize({ node, page, label: "Тест", bounds: () => ({ left: 0, top: 0, width: 1000, height: 1000 }), getBox: () => ({ ...box }), preview: (value) => { shown = value; }, commit: (value) => { box = value; commits += 1; } });
  const handle = node.children.find((child) => child.dataset.resizeDirection === "e");
  handle.dispatch("keydown", { key: "ArrowRight", preventDefault() {}, stopPropagation() {} });
  assert.equal(box.x_max, 505);
  const pointer = { button: 0, pointerId: 7, clientX: 505, clientY: 400, preventDefault() {}, stopPropagation() {} };
  handle.dispatch("pointerdown", pointer);
  handle.dispatch("pointermove", { ...pointer, pointerId: 8, clientX: 700 });
  assert.equal(commits, 1);
  handle.dispatch("pointermove", { ...pointer, clientX: 700 });
  assert.equal(shown.x_max, 700);
  resize.dispose();
  assert.equal(shown.x_max, 505);
  assert.equal(handle.hasPointerCapture(7), false);
  assert.equal(node.children.length, 0);
  handle.dispatch("pointerup", { ...pointer, clientX: 700 });
  assert.equal(commits, 1);
});
