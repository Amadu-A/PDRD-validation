// frontend/src/js/features/review/geometry.js

/**
 * Чистые преобразования координат ручных областей PDF.
 * Единая система координат: 0..1000 относительно изображения одного листа.
 */

const LIMIT = 1000;

function clamp(value) {
  return Math.max(0, Math.min(LIMIT, value));
}

/** Переводит указатель из пикселей экрана в координаты конкретного листа. */
export function normalizedPoint(clientX, clientY, imageRect) {
  if (imageRect.width <= 0 || imageRect.height <= 0) {
    throw new Error("Изображение листа ещё не загружено.");
  }

  return {
    x: Math.round(clamp((clientX - imageRect.left) * LIMIT / imageRect.width)),
    y: Math.round(clamp((clientY - imageRect.top) * LIMIT / imageRect.height)),
  };
}

/** Создаёт прямоугольник независимо от направления движения указателя. */
export function boxBetween(start, finish) {
  return {
    x_min: clamp(Math.min(start.x, finish.x)),
    y_min: clamp(Math.min(start.y, finish.y)),
    x_max: clamp(Math.max(start.x, finish.x)),
    y_max: clamp(Math.max(start.y, finish.y)),
  };
}

/** Проверяет отдельно минимальный размер области ошибки и карточки. */
export function validBox(box, { minWidth = 15, minHeight = 15 } = {}) {
  if (!box || !Object.values(box).every((value) => Number.isFinite(value))) {
    return false;
  }

  return box.x_min >= 0 && box.y_min >= 0
    && box.x_max <= LIMIT && box.y_max <= LIMIT
    && box.x_max - box.x_min >= minWidth
    && box.y_max - box.y_min >= minHeight;
}

/** Возвращает независимый от масштаба ломаный соединитель двух областей. */
export function connectorPoints(issue, callout) {
  const first = {
    x: (issue.x_min + issue.x_max) / 2,
    y: (issue.y_min + issue.y_max) / 2,
  };

  const second = {
    x: (callout.x_min + callout.x_max) / 2,
    y: (callout.y_min + callout.y_max) / 2,
  };

  const horizontal = Math.abs(second.x - first.x) >= Math.abs(second.y - first.y);

  if (horizontal) {
    const startX = second.x >= first.x ? issue.x_max : issue.x_min;
    const endX = second.x >= first.x ? callout.x_min : callout.x_max;
    const bendX = (startX + endX) / 2;

    return `${startX},${first.y} ${bendX},${first.y} ${bendX},${second.y} ${endX},${second.y}`;
  }

  const startY = second.y >= first.y ? issue.y_max : issue.y_min;
  const endY = second.y >= first.y ? callout.y_min : callout.y_max;
  const bendY = (startY + endY) / 2;

  return `${first.x},${startY} ${first.x},${bendY} ${second.x},${bendY} ${second.x},${endY}`;
}

/** Применяет нормализованную геометрию к DOM-области. */
export function positionBox(element, box) {
  element.style.left = `${box.x_min / 10}%`;
  element.style.top = `${box.y_min / 10}%`;
  element.style.width = `${(box.x_max - box.x_min) / 10}%`;
  element.style.height = `${(box.y_max - box.y_min) / 10}%`;
}