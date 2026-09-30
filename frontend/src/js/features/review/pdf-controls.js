// frontend/src/js/features/review/pdf-controls.js

/** DOM единого действия утверждения/PDF/Experience; очередь CAS и клиенты внедряются извне. */

import { requiresAreaAcceptance, reviewedPdfAvailability } from "./pdf-model.js";
import { downloadReviewedPdf } from "./download.js";
import { createExperienceApi } from "../experience/api.js";
import { captureApprovedExperience, experienceCaptureMessage } from "../experience/capture.js";

/** Соединяет утверждение, перенос Experience и скачивание с общей блокировкой Review. */
export function mountReviewedPdf({ root, jobId, api, sync, onBusy = () => {}, download = downloadReviewedPdf, experienceApi = createExperienceApi() }) {
  const button = root.querySelector("[data-analysis-pdf-reviewed]");
  if (!button) return { update() {}, dispose() {} };
  const message = document.createElement("p");
  message.className = "analysis-export__description";
  message.id = `reviewedPdfStatus-${jobId}`;
  message.setAttribute("role", "status");
  button.setAttribute("aria-describedby", message.id);
  button.after(message);
  let status = { mode: "loading" };
  let busy = false;
  let disposed = false;

  function update(next = status) {
    if (disposed) return;
    status = next;
    const availability = reviewedPdfAvailability({ ...status, busy: busy || status.busy });
    button.disabled = !availability.enabled;
    button.title = availability.message;
    const approved = status.session?.approved_revision === status.session?.revision;
    button.textContent = busy ? "Формируем итоговый PDF…" : approved && status.session ? "Скачать итоговый PDF после Human Review" : "Утвердить и скачать итоговый PDF после Human Review";
    message.textContent = availability.message;
  }

  async function click() {
    if (button.disabled || busy || disposed) return;
    busy = true;
    onBusy(true);
    update();
    try {
      let session = sync.session;
      const approvedNow = session.approved_revision !== session.revision || requiresAreaAcceptance(session);
      if (approvedNow) session = await sync.run({ action: "approve" });
      if (disposed) return;
      const capture = await captureApprovedExperience({ jobId, session, approvedNow, api: experienceApi });
      if (disposed) return;
      const result = await api.pdf(jobId, session.revision);
      if (!disposed) {
        download(result);
        message.textContent = `Итоговый PDF сформирован из утверждённой редакции. ${experienceCaptureMessage(capture)}`;
      }
    } catch (error) {
      if (!disposed) message.textContent = error.detail ?? error.message;
    } finally {
      busy = false;
      if (!disposed) {
        const text = message.textContent;
        onBusy(false);
        update();
        message.textContent = text;
      }
    }
  }
  button.addEventListener("click", click);
  update();
  return { update, dispose() { disposed = true; button.removeEventListener("click", click); } };
}
