// frontend/src/js/features/analysis/restore.js

/** Восстановление завершённого отчёта по job_id URL без повторного запуска VLM. */

import { getAnalysisResult, getAnalysisVisualization } from "./api.js";
import { renderAnalysisReport } from "./report.js";
import { appendAnnotatedPdfDownload } from "./pdf-export.js";

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

/** Загружает только явный идентификатор; пользовательские данные не кэшируются в браузере. */
export async function restoreAnalysisReport(resultView, { isCurrent = () => true } = {}) {
  const jobId = new URL(window.location.href).searchParams.get("job_id");
  if (!jobId || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(jobId)) return;
  resultView.show(`Восстанавливаем отчёт задания ${jobId}…`);
  try {
    const payload = await getAnalysisResult(jobId);
    const visualization = await getAnalysisVisualization(jobId);
    if (!isCurrent()) return;
    const report = renderAnalysisReport(payload, { jobId, visualization });
    appendAnnotatedPdfDownload(report, { jobId, payload });
    resultView.showReport(report, { jobId });
  } catch (error) {
    if (isCurrent()) resultView.showError(error);
  }
}
