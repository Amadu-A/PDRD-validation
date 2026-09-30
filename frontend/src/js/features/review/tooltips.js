// frontend/src/js/features/review/tooltips.js

/**
 * Переносит полные тултипы за обрезаемую карточку, сохраняя hover/клавиатуру.
 * У карточки нет прокрутки; собственная прокрутка полного ответа допустима.
 */

/** Подписки и вынесенные узлы принадлежат одному отображаемому отчёту. */
export function mountReviewTooltips(root) {
  const cleanups = [];
  for (const tooltip of root.querySelectorAll(".analysis-result__annotation-detail-tooltip, .analysis-result__annotation-tooltip")) {
    const owner = tooltip.parentElement;
    const trigger = owner?.querySelector(`[aria-describedby="${tooltip.id}"]`);
    if (!trigger || !document.body) continue;
    let timer = null;
    let pinned = false;
    document.body.append(tooltip);
    tooltip.classList.add("review-tooltip");
    function hide() { pinned = false; tooltip.dataset.reviewTooltipOpen = "false"; }
    function show() {
      clearTimeout(timer);
      tooltip.dataset.reviewTooltipOpen = "true";
      const bounds = trigger.getBoundingClientRect();
      const { width, height } = tooltip.getBoundingClientRect();
      tooltip.style.left = `${Math.max(8, Math.min(bounds.right - width, window.innerWidth - width - 8))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(bounds.bottom + 6, window.innerHeight - height - 8))}px`;
    }
    function scheduleHide() { if (!pinned) timer = setTimeout(hide, 120); }
    function listen(node, name, action) {
      node.addEventListener(name, action);
      cleanups.push(() => node.removeEventListener(name, action));
    }
    listen(trigger, "pointerenter", show);
    listen(trigger, "focus", show);
    listen(trigger, "click", () => {
      if (pinned) hide();
      else { pinned = true; show(); }
    });
    listen(trigger, "pointerleave", scheduleHide);
    listen(trigger, "blur", () => { pinned = false; scheduleHide(); });
    listen(tooltip, "pointerenter", () => clearTimeout(timer));
    listen(tooltip, "pointerleave", scheduleHide);
    listen(trigger, "keydown", (event) => { if (event.key === "Escape") hide(); });
    listen(window, "scroll", hide);
    listen(window, "resize", hide);
    cleanups.push(() => { clearTimeout(timer); tooltip.remove(); });
  }
  return () => { for (const cleanup of cleanups) cleanup(); };
}
