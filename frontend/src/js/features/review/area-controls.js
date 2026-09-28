// frontend/src/js/features/review/area-controls.js

/** Явная проверка области в текстовом списке; принятие замечания не заменяет её. */

import { areaConfirmationCommand } from "./pdf-model.js";

export function mountAreaConfirmations({ root, sync, snapshot, onBusy = () => {} }) {
  const buttons = new Map();
  const error = document.createElement("p");
  error.className = "review-editor__error";
  error.setAttribute("role", "alert");
  root.querySelector("[data-review-preview]")?.after(error);
  let status = { mode: "loading" };
  let busy = false;
  let disposed = false;
  const listeners = new Map();

  for (const item of root.querySelectorAll(".analysis-result__finding[data-finding-id]")) {
    const id = item.dataset.findingId;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "manual-annotation__button";
    button.dataset.reviewAreaConfirmation = id;
    item.append(button);
    buttons.set(id, button);
    const click = async () => {
      if (button.disabled || busy || disposed) return;
      const session = sync.session;
      const finding = session.findings.find((row) => row.finding_id === id);
      const area = session.area_confirmations?.find((row) => row.finding_id === id);
      const regions = snapshot().find((row) => row.finding_id === id)?.regions ?? [];
      try {
        let command;
        if (area?.valid) {
          const reason = window.prompt("Почему отзывается подтверждение области?");
          if (reason === null) return;
          if (!reason.trim()) throw new Error("Укажите причину отзыва области.");
          command = { action: "revoke_area", finding_id: id, expected_confirmation_revision: area.revision, reason: reason.trim() };
        } else {
          try { command = areaConfirmationCommand(finding, area, regions); }
          catch {
            const note = window.prompt("Вы изменили область. Укажите причину исправления координат:");
            if (note === null) return;
            command = areaConfirmationCommand(finding, area, regions, note);
          }
        }
        busy = true;
        onBusy(true);
        update();
        await sync.run(command);
        if (!disposed) error.textContent = "";
      } catch (problem) {
        if (!disposed) error.textContent = problem.detail ?? problem.message;
      } finally {
        busy = false;
        if (!disposed) {
          onBusy(false);
          update();
        }
      }
    };
    button.addEventListener("click", click);
    listeners.set(button, click);
  }

  function update(next = status) {
    if (disposed) return;
    status = next;
    for (const [id, button] of buttons) {
      const finding = status.session?.findings.find((row) => row.finding_id === id);
      const area = status.session?.area_confirmations?.find((row) => row.finding_id === id);
      const regions = finding?.display_regions ?? finding?.proposed_regions.map((row) => row.bbox) ?? [];
      button.hidden = status.mode === "local" || !finding || finding.origin !== "vlm";
      button.disabled = busy || status.busy || status.mode !== "saved" || status.pending > 0 || (!regions.length && !area?.valid);
      button.textContent = area?.valid ? "Область проверена ✓ · Отозвать" : regions.length ? "Подтвердить область" : "Без области · только в тексте PDF";
      button.title = "Подтвердите соответствие рамки замечанию на листе. После правки текста или геометрии требуется повторная проверка.";
      button.setAttribute("aria-pressed", String(Boolean(area?.valid)));
    }
  }
  update();
  return { update, dispose() {
    disposed = true;
    for (const [button, listener] of listeners) button.removeEventListener("click", listener);
    error.remove();
  } };
}
