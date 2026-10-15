// frontend/src/js/features/analysis/visualization-review.js

/**
 * Связи элементов автоматической визуализации для внешнего Review-адаптера.
 * WeakMap не удерживает удалённый отчёт. Исходный payload здесь не изменяется.
 * Пользовательские размеры карточек сохраняются при перерасчёте раскладки.
 */

const pages = new WeakMap();
const pinnedCallouts = new WeakMap();

/** Регистрирует связи после построения конкретного листа. */
export function registerVisualizationReview(page, adapter) {
  pages.set(page, adapter);
}

/** Возвращает связи только этого экземпляра листа, без поиска в общем DOM. */
export function getVisualizationReview(page) {
  return pages.get(page) ?? null;
}

/** Закрепляет выбранное пользователем место; null возвращает автоматическую раскладку. */
export function pinReviewCallout(callout, box) {
  if (box) pinnedCallouts.set(callout, { ...box });
  else pinnedCallouts.delete(callout);
}

/** Читает независимую копию закреплённых размеров для перерасчёта масштаба. */
export function reviewCalloutBox(callout) {
  const box = pinnedCallouts.get(callout);
  return box ? { ...box } : null;
}

/** Меняет состав рамок сохранённой находки, сохраняя связи соседей общей карточки. */
export function replaceReviewRegions(adapter, record, boxes) {
  const group = adapter.groups?.find((item) => item.callout === record.callout);
  if (!group || !adapter.createReviewBoxes || !adapter.svg) {
    throw new Error("Визуализация не поддерживает восстановление областей Review.");
  }
  const next = adapter.createReviewBoxes(record, boxes);
  const connectors = next.map(({ node }) => {
    node.dataset.findingId = record.findingId;
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.classList.add("analysis-result__connector");
    return { bboxNode: node, polyline };
  });
  for (const { node } of record.bboxEntries) node.remove();
  for (const { polyline } of record.connectorEntries) polyline.remove();
  record.bboxEntries.splice(0, record.bboxEntries.length, ...next);
  record.connectorEntries.splice(0, record.connectorEntries.length, ...connectors);
  for (const { node } of next) adapter.imagePane.append(node);
  for (const { polyline } of connectors) adapter.svg.append(polyline);
  const members = adapter.records.filter((item) => item.callout === record.callout);
  group.bboxEntries.splice(0, group.bboxEntries.length, ...members.flatMap((item) => item.bboxEntries));
  group.connectorEntries.splice(0, group.connectorEntries.length, ...members.flatMap((item) => item.connectorEntries));
  adapter.bindReviewBoxes(record);
  if (record.item === record.callout) {
    const message = record.callout.querySelector(".analysis-result__annotation-unlocated");
    if (next.length) message?.remove();
    else if (!message) {
      const text = document.createElement("span");
      text.className = "analysis-result__annotation-unlocated";
      text.textContent = "Точное место на листе не определено.";
      record.callout.append(text);
    }
  }
}
