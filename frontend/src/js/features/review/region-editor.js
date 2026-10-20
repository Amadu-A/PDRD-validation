// frontend/src/js/features/review/region-editor.js

/** Ручная локализация существующего finding: исходные доказательства и ID не меняются. */
import { normalizedPoint, boxBetween, validBox, positionBox } from "./geometry.js";

export function attachRegionEditor({ entry, enabled, onStart, onChange }) {
  const tools = document.createElement("div");
  tools.className = "review-geometry__tools";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "review-controls__button";
  add.textContent = "+ Область";
  add.dataset.reviewRegionAdd = entry.findingId;
  add.setAttribute("aria-label", "Указать дополнительную область этого замечания");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "review-controls__button";
  cancel.textContent = "Отмена";
  cancel.hidden = true;
  tools.append(add, cancel);
  entry.callout.append(tools);
  let layer = null;
  let draft = null;
  let drag = null;
  const removers = [];

  function finish() {
    const previous = drag;
    drag = null;
    if (previous && layer?.hasPointerCapture(previous.pointerId)) layer.releasePointerCapture(previous.pointerId);
    layer?.remove();
    layer = null;
    entry.page.dataset.reviewSelecting = "false";
    cancel.hidden = true;
  }

  function refreshAccess() {
    const disabled = !enabled();
    add.disabled = disabled;
    for (const button of removers) button.disabled = disabled;
    if (disabled) finish();
  }

  globalThis.window?.addEventListener?.("resize", finish);

  cancel.addEventListener("click", finish);
  add.addEventListener("click", (event) => {
    event.stopPropagation?.();
    if (!enabled()) return;
    onStart();
    const pane = entry.adapter.imagePane;
    const bounds = entry.adapter.image.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    layer = document.createElement("div");
    layer.className = "review-geometry__layer";
    layer.tabIndex = -1;
    layer.setAttribute("aria-label", "Выделите область замечания; Escape отменяет выделение");
    draft = document.createElement("div");
    draft.className = "manual-annotation__draft";
    draft.hidden = true;
    layer.append(draft);
    const parent = pane.getBoundingClientRect();
    Object.assign(layer.style, {
      left: `${bounds.left - parent.left}px`, top: `${bounds.top - parent.top}px`,
      width: `${bounds.width}px`, height: `${bounds.height}px`,
    });
    pane.append(layer);
    entry.page.dataset.reviewSelecting = "true";
    cancel.hidden = false;
    layer.addEventListener("keydown", (key) => { if (key.key === "Escape") finish(); });
    layer.addEventListener("pointerdown", (pointer) => {
      if (pointer.button !== 0 || !enabled()) return;
      pointer.preventDefault();
      drag = { pointerId: pointer.pointerId, start: normalizedPoint(pointer.clientX, pointer.clientY, bounds) };
      layer.setPointerCapture(pointer.pointerId);
    });
    layer.addEventListener("pointermove", (pointer) => {
      if (!drag || drag.pointerId !== pointer.pointerId) return;
      draft.hidden = false;
      positionBox(draft, boxBetween(drag.start, normalizedPoint(pointer.clientX, pointer.clientY, bounds)));
    });
    layer.addEventListener("pointerup", (pointer) => {
      if (!drag || drag.pointerId !== pointer.pointerId) return;
      const box = boxBetween(drag.start, normalizedPoint(pointer.clientX, pointer.clientY, bounds));
      const allowed = enabled() && validBox(box);
      finish();
      if (allowed) onChange([...entry.boxes.slice(0, entry.editableCount), box]);
    });
    layer.addEventListener("pointercancel", finish);
    layer.addEventListener("lostpointercapture", finish);
    layer.focus();
  });

  for (let index = 0; index < entry.editableCount; index += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "review-controls__button review-geometry__remove";
    button.textContent = "×";
    button.dataset.reviewRegionRemove = String(index);
    button.setAttribute("aria-label", `Удалить область ${index + 1} этого замечания`);
    button.addEventListener("click", (event) => {
      event.stopPropagation?.();
      if (!enabled()) return;
      onStart();
      onChange(entry.boxes.slice(0, entry.editableCount).filter((_, number) => number !== index));
    });
    entry.bboxEntries[index].node.append(button);
    removers.push(button);
  }
  refreshAccess();
  return {
    cancel: finish, refreshAccess,
    dispose() {
      finish(); globalThis.window?.removeEventListener?.("resize", finish);
      tools.remove(); for (const button of removers) button.remove();
    },
  };
}
