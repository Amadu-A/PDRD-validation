// frontend/tests/review-restore-geometry.test.js

/** Регрессии исходных, сохранённых и межстраничных рамок без подмены областей другого листа. */

import assert from "node:assert/strict";
import test from "node:test";
import { restoredReviewGeometry } from "../src/js/features/review/restore-geometry.js";

const box = (x) => ({ x_min: x, y_min: 100, x_max: x + 40, y_max: 160 });
const region = (x) => ({ bbox: box(x) });

test("сохранённая основная геометрия имеет приоритет при любом числе рамок рендера", () => {
  for (const count of [0, 1, 2, 4]) {
    const saved = Array.from({ length: count }, (_, i) => box(100 + i * 80));
    assert.deepEqual(restoredReviewGeometry({ pageNumber: 7, boxes: [box(800)] }, {
      proposed_regions: [region(200)], display_regions: saved,
    }), { boxes: saved, editableCount: count });
  }
});

test("вторичное доказательство выбирается по физической странице и остаётся без права правки", () => {
  assert.deepEqual(restoredReviewGeometry({ pageNumber: 10, secondaryEvidence: true }, {
    proposed_regions: [region(100)], display_regions: [box(200)],
    evidence_locations: [{ page: 7, proposed_regions: [region(300)] }, { page: 10, proposed_regions: [region(400), region(500)] }],
  }), { boxes: [box(400), box(500)], editableCount: 0 });
});

test("D сохраняет собственные доказательства и не возвращает исходную рамку после её правки", () => {
  const row = { proposed_regions: [region(100)], display_regions: [box(200)],
    evidence_locations: [{ page: 7, proposed_regions: [region(100), region(300)] }],
    document_context_basis_sources: [{ page: 7, visual_regions: [region(300), region(400)] }, { page: 10, visual_regions: [region(500)] }],
  };
  const before = structuredClone(row);
  assert.deepEqual(restoredReviewGeometry({ pageNumber: 7, documentEvidence: true, boxes: [] }, row), {
    boxes: [box(200), box(300), box(400)], editableCount: 1,
  });
  assert.deepEqual(row, before);
});

test("старый D-снимок сохраняет дополнительные рамки чтения после четырёх основных", () => {
  const boxes = Array.from({ length: 6 }, (_, i) => box(100 + i * 80));
  assert.deepEqual(restoredReviewGeometry({ pageNumber: 7, documentEvidence: true, boxes }, {
    proposed_regions: boxes.slice(0, 4).map((bbox) => ({ bbox })),
  }), { boxes, editableCount: 4 });
});

test("неизвестная локализация не заимствует основную рамку у соседней страницы", () => {
  assert.deepEqual(restoredReviewGeometry({ pageNumber: 10, secondaryEvidence: true }, {
    proposed_regions: [region(100)],
  }), { boxes: [], editableCount: 0 });
});

test("повреждённые координаты не маскируются успешным восстановлением", () => {
  for (const value of [NaN, Infinity, -1, 1001, "100", null]) {
    assert.throws(() => restoredReviewGeometry({ pageNumber: 7 }, {
      proposed_regions: [], display_regions: [{ ...box(100), x_min: value }],
    }), /неверную геометрию/);
  }
});
