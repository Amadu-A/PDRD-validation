// frontend/src/js/features/review/persistence.js

/**
 * Связывает DOM Review с отдельной очередью API и состояниями сохранения.
 * Загрузка серверной версии явно сбрасывает несохранённые изменения страницы.
 */

import { createReviewController } from "./controller.js";
import { createReviewApi } from "./api.js";
import { reviewEntries } from "./commands.js";
import { createReviewSync } from "./sync.js";
import { mountReviewTooltips } from "./tooltips.js";

const MESSAGES = {
  loading: "Восстанавливаем решения с сервера…",
  local: "Локальный предпросмотр: серверное сохранение доступно на закрытом фронте Review. Решения не влияют на PDF.",
  saving: "Сохраняем изменения… Не закрывайте страницу.",
  saved: "Изменения сохранены на сервере и восстановятся после перезагрузки.",
  error: "Не удалось сохранить или загрузить Review. Несохранённые изменения остаются на странице.",
  conflict: "Review изменён в другой вкладке. Несохранённые изменения остаются на странице; загрузите серверную версию.",
};

/** Монтирует новую независимую очередь при смене задания или отчёта. */
export function createReviewPersistence({ api = createReviewApi() } = {}) {
  let active = null;
  const sessions = new Set();
  const hasPending = () => [...sessions].some((session) => session.pending > 0);
  window.addEventListener("beforeunload", (event) => {
    if (hasPending()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  function clear() {
    if (!active) return;
    active.sync?.detach();
    if (active.sync?.pending === 0) sessions.delete(active.sync);
    active.controller.dispose();
    active.tooltips();
    active = null;
  }

  function mount(root, { jobId = null } = {}) {
    clear();
    let sync = null;
    const controller = createReviewController({ onChange: () => sync?.changed() });
    controller.mount(root);
    const tooltips = mountReviewTooltips(root);
    active = { controller, tooltips, sync: null };
    if (!jobId || !root.querySelector("[data-review-preview]")) return;
    const url = new URL(window.location.href);
    url.searchParams.set("job_id", jobId);
    window.history.replaceState(null, "", url);
    const actions = document.createElement("div");
    actions.className = "review-sync";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "manual-annotation__button";
    retry.textContent = "Повторить сохранение";
    retry.hidden = true;
    const reload = document.createElement("button");
    reload.type = "button";
    reload.className = "manual-annotation__button";
    reload.textContent = "Загрузить серверную версию (сбросить несохранённое)";
    reload.hidden = true;
    reload.addEventListener("click", () => window.location.reload());
    actions.append(retry, reload);
    root.querySelector("[data-review-preview]").after(actions);
    sync = createReviewSync({
      jobId, api,
      snapshot: () => reviewEntries(controller.getReviewSnapshot(), controller.getManualSnapshot()),
      hydrate: controller.hydrate,
      onStatus(status) {
        controller.setConnection({
          isPersistent: status.mode !== "local",
          isLocked: ["loading", "error", "conflict"].includes(status.mode),
          message: MESSAGES[status.mode] + (status.error?.detail ? ` ${String(status.error.detail).slice(0, 300)}` : ""),
        });
        retry.hidden = status.mode !== "error" || status.pending === 0;
        reload.hidden = !["error", "conflict", "saved"].includes(status.mode);
      },
    });
    active.sync = sync;
    sessions.add(sync);
    retry.addEventListener("click", () => { void sync.retry(); });
    void sync.start();
  }

  return { mount, clear, hasPending };
}
