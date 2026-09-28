// frontend/src/js/features/review/resize.js

/**
 * DOM-адаптер растягивания за границы: захват указателя, предварительный показ,
 * сохранение при отпускании и отмена через Esc/pointercancel. Не обращается к API.
 */

import { RESIZE_DIRECTIONS, resizedBox, sameBox } from "./resize-geometry.js";

const LABELS = {
  n: "верх", e: "право", s: "низ", w: "лево",
  ne: "верх справа", se: "низ справа", sw: "низ слева", nw: "верх слева",
};

/** Возвращает очистку подписок и отмену незавершённого жеста. */
export function attachBoxResize({
  node, page, bounds, getBox, preview, commit, minimum, label,
  enabled = () => true,
  onStart = () => {},
  rollback = preview,
  scrollContent = false,
}) {
  const cleanups = [];
  const handles = [];
  let drag = null;
  let content = null;
  if (scrollContent) {
    content = document.createElement("div");
    content.className = "review-resize__content";
    content.append(...node.children);
    node.append(content);
    node.classList.add("review-resize__box");
  }

  function listen(target, event, handler) {
    target.addEventListener(event, handler);
    cleanups.push(() => target.removeEventListener(event, handler));
  }

  function finish(cancelled = false) {
    if (!drag) return;
    const previous = drag;
    drag = null;
    if (previous.handle.hasPointerCapture(previous.pointerId)) {
      previous.handle.releasePointerCapture(previous.pointerId);
    }
    node.classList.remove("review-resize--active");
    if (cancelled || sameBox(previous.box, previous.next)) {
      rollback(previous.box);
    } else {
      try {
        commit(previous.next, previous.box);
      } catch (error) {
        rollback(previous.box);
        throw error;
      }
    }
  }

  for (const direction of RESIZE_DIRECTIONS) {
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = `review-resize__handle review-resize__handle--${direction}`;
    handle.dataset.resizeDirection = direction;
    handle.tabIndex = 0;
    handle.setAttribute("aria-label", `${label}: ${LABELS[direction]}. Стрелки — изменить размер, Esc — отменить.`);
    handle.title = `${label}: потяните границу`;
    node.append(handle);
    handles.push(handle);

    listen(handle, "pointerdown", (event) => {
      if (event.button !== 0 || drag || !enabled()) return;
      const rect = bounds();
      if (rect.width <= 0 || rect.height <= 0) return;
      event.preventDefault();
      event.stopPropagation();
      onStart();
      const box = getBox();
      drag = {
        handle, direction, pointerId: event.pointerId,
        x: event.clientX, y: event.clientY, rect, box, next: box,
      };
      handle.setPointerCapture(event.pointerId);
      node.classList.add("review-resize--active");
    });
    const move = (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      drag.next = resizedBox(drag.box, drag.direction, {
        x: Math.round((event.clientX - drag.x) * 1000 / drag.rect.width),
        y: Math.round((event.clientY - drag.y) * 1000 / drag.rect.height),
      }, minimum);
      preview(drag.next);
    };
    listen(handle, "pointermove", move);
    listen(handle, "pointerup", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      move(event);
      finish();
    });
    listen(handle, "pointercancel", () => finish(true));
    listen(handle, "lostpointercapture", () => finish(true));
    listen(handle, "keydown", (event) => {
      const delta = {
        ArrowLeft: { x: -5, y: 0 }, ArrowRight: { x: 5, y: 0 },
        ArrowUp: { x: 0, y: -5 }, ArrowDown: { x: 0, y: 5 },
      }[event.key];
      if (!delta || drag || !enabled()) return;
      event.preventDefault();
      event.stopPropagation();
      onStart();
      const box = getBox();
      const next = resizedBox(box, direction, delta, minimum);
      if (!sameBox(box, next)) commit(next, box);
    });
  }

  listen(page, "keydown", (event) => {
    if (event.key === "Escape" && drag) {
      event.preventDefault();
      finish(true);
    }
  });

  return {
    cancel: () => finish(true),
    dispose() {
      finish(true);
      for (const cleanup of cleanups) cleanup();
      for (const handle of handles) handle.remove();
      if (content) {
        node.append(...content.children);
        content.remove();
        node.classList.remove("review-resize__box");
      }
    },
  };
}
