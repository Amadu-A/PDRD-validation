// frontend/src/js/features/review/persistence.js

/**
 * Связывает DOM Review с отдельной очередью API и состояниями сохранения.
 * При ошибке несохранённая очередь остаётся на странице и защищена от закрытия.
 */

import { createReviewController } from "./controller.js";
import { createReviewApi } from "./api.js";
import { reviewEntries } from "./commands.js";
import { createReviewSync } from "./sync.js";
import { mountReviewTooltips } from "./tooltips.js";
import { mountReviewedPdf } from "./pdf-controls.js";

const MESSAGES = {
  loading: "Восстанавливаем решения с сервера…",
  local: "Серверное сохранение Review недоступно. Итоговый PDF можно скачать после восстановления сервиса.",
  saving: "Сохраняем изменения… Не закрывайте страницу.",
  saved: "Изменения сохранены на сервере и восстановятся после перезагрузки.",
  error: "Не удалось сохранить или загрузить Review. Несохранённые изменения остаются на странице.",
  conflict: "Review изменён в другой вкладке. Несохранённые изменения остаются на странице; сверяйте их перед обновлением страницы.",
};

/** Монтирует новую независимую очередь при смене задания или отчёта. */
export function createReviewPersistence({
  api = createReviewApi(),
  getCapabilities = () => ({ canCreateGold: true, canDecide: true, canDownloadReview: true }),
} = {}) {
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
    else if (active.sync) {
      const previous = active.sync;
      void previous.settled().then(() => { if (previous.pending === 0) sessions.delete(previous); });
    }
    active.controller.dispose();
    active.tooltips();
    active.pdf?.dispose();
    active = null;
  }

  function mount(root, { jobId = null } = {}) {
    clear();
    let sync = null;
    let pdf = null;
    let busy = false;
    let latestStatus = { mode: "loading" };
    let capabilities = getCapabilities();
    const controller = createReviewController({ onChange: () => sync?.changed(), capabilities });
    controller.mount(root);
    const tooltips = mountReviewTooltips(root);
    active = { controller, tooltips, sync: null };
    if (!jobId || !root.querySelector("[data-review-preview]")) return;
    const url = new URL(window.location.href);
    url.searchParams.set("job_id", jobId);
    window.history.replaceState(null, "", url);
    function applyStatus(status = latestStatus) {
      latestStatus = status;
      if (status.session?.decision_history) controller.setHistory(status.session.decision_history);
      controller.setConnection({
        isPersistent: status.mode !== "local",
        isLocked: busy || ["loading", "error", "conflict"].includes(status.mode),
        message: MESSAGES[status.mode] + (status.error ? ` ${String(status.error.detail ?? status.error.message).slice(0, 300)}` : ""),
      });
      pdf?.update({ ...status, busy });
    }
    const onBusy = (value) => { busy = value; applyStatus(); };
    const snapshot = () => reviewEntries(controller.getReviewSnapshot(), controller.getManualSnapshot());
    sync = createReviewSync({
      jobId, api,
      snapshot,
      hydrate: controller.hydrate,
      onStatus: applyStatus,
    });
    if (capabilities.canDownloadReview !== false) {
      pdf = mountReviewedPdf({ root, jobId, api, sync, onBusy });
    }
    active.pdf = pdf;
    active.sync = sync;
    active.setCapabilities = (next) => {
      capabilities = next;
      controller.setCapabilities(next);
      if (capabilities.canDownloadReview === false) { pdf?.dispose(); pdf = null; }
      else if (!pdf) pdf = mountReviewedPdf({ root, jobId, api, sync, onBusy });
      active.pdf = pdf;
      applyStatus();
    };
    sessions.add(sync);
    void sync.start();
  }

  return { mount, clear, hasPending, setCapabilities(next) { active?.setCapabilities?.(next); } };
}
