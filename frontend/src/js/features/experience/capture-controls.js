// frontend/src/js/features/experience/capture-controls.js

/** Явное утверждение и повтор переноса проверенных примеров, отдельно от скачивания PDF. */
import { reviewedPdfAvailability } from "../review/pdf-model.js";
import { createExperienceApi } from "./api.js";

export function mountExperienceCapture({ root, jobId, sync, onBusy = () => {}, api = createExperienceApi() }) {
  const anchor = root.querySelector("[data-analysis-pdf-reviewed]");
  if (!anchor) return null;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "analysis-export__pending";
  button.dataset.experienceCapture = "";
  const message = document.createElement("p");
  message.className = "analysis-export__description";
  message.id = "experienceCaptureStatus";
  message.setAttribute("role", "status");
  button.setAttribute("aria-describedby", message.id);
  anchor.after(button, message);
  let status = { mode: "loading" };
  let busy = false;
  let disposed = false;

  function update(next = status) {
    if (disposed) return;
    status = next;
    button.disabled = busy || !reviewedPdfAvailability({ ...status, busy: status.busy }).enabled;
    button.textContent = busy ? "Сохраняем проверенные примеры…" : "Сохранить в базу опыта";
    const capture = status.session?.experience_capture;
    message.textContent = capture?.status === "error" ? capture.message
      : capture?.status === "saved" ? `Experience сохранён: пригодных примеров ${capture.eligible}, без проверенной области или решения ${capture.excluded}.`
        : "В базу опыта попадут только рассмотренные замечания с подтверждёнными областями. Сохранение утверждает текущий Review.";
    button.hidden = status.mode === "local";
    message.hidden = status.mode === "local";
  }

  async function click() {
    if (button.disabled || busy || disposed) return;
    busy = true;
    onBusy(true);
    update();
    let resultText;
    try {
      let session = sync.session;
      const needsApproval = session.approved_revision !== session.revision;
      if (needsApproval) session = await sync.run({ action: "approve" });
      const result = needsApproval && session.experience_capture?.status === "saved"
        ? session.experience_capture : await api.capture(jobId, session.revision);
      resultText = `Experience сохранён: пригодных ${result.eligible}; исключено ${result.excluded}; новых ${result.created}.`;
    } catch (error) {
      resultText = error.detail ?? error.message;
    } finally {
      busy = false;
      if (!disposed) { onBusy(false); update(); message.textContent = resultText; }
    }
  }
  button.addEventListener("click", click);
  update();
  return { update, dispose() { disposed = true; button.removeEventListener("click", click); button.remove(); message.remove(); } };
}
