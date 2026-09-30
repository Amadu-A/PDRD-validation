// frontend/tests/review-geometry.test.js

/** Тесты координат и соединителя ручного замечания. */

import assert from "node:assert/strict";
import test from "node:test";

import {
  boxBetween,
  connectorPoints,
  normalizedPoint,
  positionBox,
  validBox,
} from "../src/js/features/review/geometry.js";

test("перевод координат ограничен ровно одним изображением", () => {
  const rect = {
    left: 100,
    top: 200,
    width: 800,
    height: 400,
  };

  assert.deepEqual(
    normalizedPoint(300, 300, rect),
    { x: 250, y: 250 },
  );

  assert.deepEqual(
    normalizedPoint(0, 800, rect),
    { x: 0, y: 1000 },
  );

  assert.throws(
    () => normalizedPoint(
      1,
      2,
      { width: 0, height: 100 },
    ),
  );
});

test("области можно выделять в любом направлении", () => {
  assert.deepEqual(
    boxBetween(
      { x: 500, y: 600 },
      { x: 100, y: 50 },
    ),
    {
      x_min: 100,
      y_min: 50,
      x_max: 500,
      y_max: 600,
    },
  );
});

test("малые области и неверные координаты отбрасываются", () => {
  const box = {
    x_min: 20,
    y_min: 30,
    x_max: 110,
    y_max: 120,
  };

  assert.equal(validBox(box), true);

  assert.equal(
    validBox(
      box,
      { minWidth: 130, minHeight: 80 },
    ),
    false,
  );

  assert.equal(
    validBox({ ...box, x_max: 2000 }),
    false,
  );

  assert.equal(
    validBox({ ...box, y_min: NaN }),
    false,
  );

  assert.equal(validBox(null), false);
});

test("соединитель направляется от края ошибки к краю карточки", () => {
  const issue = {
    x_min: 100,
    y_min: 100,
    x_max: 200,
    y_max: 200,
  };

  const right = {
    x_min: 500,
    y_min: 100,
    x_max: 650,
    y_max: 200,
  };

  const bottom = {
    x_min: 100,
    y_min: 500,
    x_max: 200,
    y_max: 650,
  };

  assert.equal(
    connectorPoints(issue, right),
    "200,150 350,150 350,150 500,150",
  );

  assert.equal(
    connectorPoints(issue, bottom),
    "150,200 150,350 150,350 150,500",
  );
});

test("позиции являются процентами и устойчивы к изменению размеров", () => {
  const item = {
    style: {},
  };

  positionBox(item, {
    x_min: 100,
    y_min: 200,
    x_max: 400,
    y_max: 600,
  });

  assert.deepEqual(
    item.style,
    {
      left: "10%",
      top: "20%",
      width: "30%",
      height: "40%",
    },
  );
});