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
import { mountReviewedPdf } from "./pdf-controls.js";
import { mountAreaConfirmations } from "./area-controls.js";
import { mountExperienceCapture } from "../experience/capture-controls.js";

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
    else if (active.sync) {
      const previous = active.sync;
      void previous.settled().then(() => { if (previous.pending === 0) sessions.delete(previous); });
    }
    active.controller.dispose();
    active.tooltips();
    active.pdf?.dispose();
    active.areas?.dispose();
    active.experience?.dispose();
    active = null;
  }

  function mount(root, { jobId = null } = {}) {
    clear();
    let sync = null;
    let pdf = null;
    let areas = null;
    let experience = null;
    let busy = false;
    let latestStatus = { mode: "loading" };
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
    function applyStatus(status = latestStatus) {
      latestStatus = status;
      controller.setConnection({
        isPersistent: status.mode !== "local",
        isLocked: busy || ["loading", "error", "conflict"].includes(status.mode),
        message: MESSAGES[status.mode] + (status.error?.detail ? ` ${String(status.error.detail).slice(0, 300)}` : ""),
      });
      retry.hidden = status.mode !== "error" || status.pending === 0;
      reload.hidden = !["error", "conflict", "saved"].includes(status.mode);
      pdf?.update({ ...status, busy });
      areas?.update({ ...status, busy });
      experience?.update({ ...status, busy });
    }
    const onBusy = (value) => { busy = value; applyStatus(); };
    const snapshot = () => reviewEntries(controller.getReviewSnapshot(), controller.getManualSnapshot());
    sync = createReviewSync({
      jobId, api,
      snapshot,
      hydrate: controller.hydrate,
      onStatus: applyStatus,
    });
    pdf = mountReviewedPdf({ root, jobId, api, sync, onBusy });
    areas = mountAreaConfirmations({ root, sync, snapshot, onBusy });
    experience = mountExperienceCapture({ root, jobId, sync, onBusy });
    active.pdf = pdf;
    active.areas = areas;
    active.experience = experience;
    active.sync = sync;
    sessions.add(sync);
    retry.addEventListener("click", () => { void sync.retry(); });
    void sync.start();
  }

  return { mount, clear, hasPending };
}
