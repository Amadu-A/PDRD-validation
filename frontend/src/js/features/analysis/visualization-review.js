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
