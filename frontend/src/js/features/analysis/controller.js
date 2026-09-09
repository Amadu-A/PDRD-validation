// frontend/src/js/features/analysis/controller.js

/**
 * Оркестрация пользовательского запуска анализа.
 *
 * Модуль связывает form, Analysis API, polling, modal и result view,
 * но не занимается поиском DOM-элементов.
 */

import {
  getAnalysisResult,
  submitAnalysis,
} from "./api.js";

import {
  statusLabel,
} from "./labels.js";

import {
  waitForAnalysis,
} from "./polling.js";

import {
  renderAnalysisReport,
} from "./report.js";


/**
 * Создаёт controller длительного анализа.
 *
 * @param {object} dependencies Runtime dependencies.
 * @param {object} dependencies.analysisForm Form controller.
 * @param {object} dependencies.modal Modal controller.
 * @param {object} dependencies.resultView Result view.
 * @returns {{submit: Function}} Analysis controller.
 */
export function createAnalysisController({
  analysisForm,
  modal,
  resultView,
}) {
  function renderProgress(
    jobId,
    payload,
    elapsedSeconds,
  ) {
    const status = statusLabel(
      payload.status,
    );

    modal.show(
      `${status}. Прошло ${elapsedSeconds} сек.`,
    );

    resultView.show(
      `Задание: ${jobId}\n`
      + `Статус: ${status}\n`
      + `Попытка worker: ${payload.attempt_count ?? 0}\n`
      + `Прошло: ${elapsedSeconds} сек.`,
    );
  }


  async function submit(
    event,
  ) {
    event.preventDefault();

    const validation = (
      analysisForm.validate()
    );

    if (!validation.valid) {
      if (validation.message) {
        resultView.show(
          validation.message,
        );
      }

      return;
    }

    modal.clearJobId();

    modal.show(
      "Документы загружаются в API Gateway…",
    );

    resultView.show(
      "Отправляем документы в API Gateway…",
    );

    try {
      const accepted = await submitAnalysis(
        analysisForm.toFormData(),
      );

      const jobId = accepted.job_id;

      if (!jobId) {
        throw new Error(
          "API Gateway не вернул job_id.",
        );
      }

      modal.setJobId(
        jobId,
      );

      resultView.show(
        `Задание: ${jobId}\n`
        + `Статус: ${statusLabel(accepted.status)}`,
      );

      await waitForAnalysis(
        jobId,
        {
          onProgress: ({
            payload,
            elapsedSeconds,
          }) => {
            renderProgress(
              jobId,
              payload,
              elapsedSeconds,
            );
          },
        },
      );

      modal.show(
        "Анализ завершён. Загружаем результат…",
      );

      const payload = await getAnalysisResult(
        jobId,
      );

      resultView.showReport(
        renderAnalysisReport(
          payload,
          {
            jobId,
          },
        ),
      );

    } catch (error) {
      resultView.showError(
        error,
      );

    } finally {
      modal.hide();
    }
  }


  return {
    submit,
  };
}