// frontend/src/js/features/analysis/restore.js

/** Восстановление задания по ссылке или URL без повторного запуска VLM. */

import { getAnalysisResult, getAnalysisVisualization } from "./api.js";
import { consumeGuestAccessFromUrl } from "./guest-access.js";
import { waitForAnalysis } from "./polling.js";
import { renderAnalysisReport } from "./report.js";
import { appendAnnotatedPdfDownload } from "./pdf-export.js";
import { appendGuestShareLink } from "./share-link.js";

/** Отменяет восстановление старого отчёта, если пользователь начал новое задание. */
export function bindReportRestoration({ resultView, formElement, submit, canSubmit = () => true }) {
  let current = true;
  formElement.addEventListener("submit", (event) => {
    if (!canSubmit()) {
      event.preventDefault();
      return;
    }
    current = false;
    submit(event);
  });
  void restoreAnalysisReport(resultView, { isCurrent: () => current });
}

/** Извлекает временный ключ, ждёт незавершённое задание и загружает его результат. */
export async function restoreAnalysisReport(resultView, { isCurrent = () => true } = {}) {
  const jobId = consumeGuestAccessFromUrl(window.location, window.history);
  if (!jobId || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(jobId)) return;
  resultView.show(`Восстанавливаем отчёт задания ${jobId}…`);
  try {
    const finalStatus = await waitForAnalysis(jobId, {
      onProgress: ({ payload }) => {
        if (isCurrent()) resultView.show(`Задание ${jobId}: ${payload.status}. Ожидаем результат…`);
      },
    });
    if (!isCurrent()) return;
    if (finalStatus.status === "cancelled") {
      resultView.show("Анализ этого задания был отменён.");
      return;
    }
    const payload = await getAnalysisResult(jobId);
    const visualization = !payload.source_artifacts_expired && ["pdf_only", "pdf_cad"].includes(payload.source_mode)
      ? await getAnalysisVisualization(jobId) : null;
    if (!isCurrent()) return;
    const report = renderAnalysisReport(payload, { jobId, visualization });
    if (payload.source_artifacts_expired) {
      const notice = document.createElement("p");
      notice.textContent = payload.source_artifacts_message || "Исходные файлы удалены по сроку хранения. Результат и Human Review сохранены.";
      report.prepend(notice);
    }
    appendAnnotatedPdfDownload(report, { jobId, payload });
    appendGuestShareLink(report, jobId);
    resultView.showReport(report, { jobId });
  } catch (error) {
    if (isCurrent()) resultView.showError(error);
  }
}
