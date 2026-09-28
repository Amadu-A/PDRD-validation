// frontend/src/js/features/review/resize-geometry.js

/** Чистый расчёт растягивания стороны/угла в координатах одного листа 0..1000. */

export const RESIZE_DIRECTIONS = Object.freeze(["n", "e", "s", "w", "ne", "se", "sw", "nw"]);

/** Противоположная сторона неподвижна; рамка не выворачивается и не выходит за лист. */
export function resizedBox(box, direction, delta, { minWidth = 15, minHeight = 15 } = {}) {
  if (!RESIZE_DIRECTIONS.includes(direction)) {
    throw new Error("Неизвестная граница области.");
  }
  const result = { ...box };
  const width = Math.min(minWidth, box.x_max - box.x_min);
  const height = Math.min(minHeight, box.y_max - box.y_min);
  if (direction.includes("w")) result.x_min = Math.max(0, Math.min(box.x_max - width, box.x_min + delta.x));
  if (direction.includes("e")) result.x_max = Math.min(1000, Math.max(box.x_min + width, box.x_max + delta.x));
  if (direction.includes("n")) result.y_min = Math.max(0, Math.min(box.y_max - height, box.y_min + delta.y));
  if (direction.includes("s")) result.y_max = Math.min(1000, Math.max(box.y_min + height, box.y_max + delta.y));
  return result;
}

/** Сравнивает только четыре координаты, без зависимостей от DOM. */
export function sameBox(left, right) {
  return ["x_min", "y_min", "x_max", "y_max"].every((key) => left[key] === right[key]);
}
