// frontend/src/js/features/analysis/visualization-selection.js

/** Выбор одного finding во всём отчёте, включая его доказательства на других листах. */
export function createFindingSelection() {
  const items = new Set();
  let selected = null;
  let hovered = null;

  function redraw() {
    const active = hovered ?? selected;
    for (const item of items) {
      const enabled = item.callout.dataset.findingId === active;
      item.callout.classList.toggle("analysis-result__annotation--active", enabled);
      for (const { node } of item.bboxEntries) node.classList.toggle("analysis-result__bbox--active", enabled);
      for (const { polyline } of item.connectorEntries) polyline.classList.toggle("analysis-result__connector--active", enabled);
    }
  }

  return {
    register(item) { items.add(item); redraw(); },
    select(id) { selected = id; hovered = null; redraw(); },
    enter(id) { hovered = id; redraw(); },
    leave(id) { if (hovered === id) hovered = null; redraw(); },
    redraw,
  };
}
