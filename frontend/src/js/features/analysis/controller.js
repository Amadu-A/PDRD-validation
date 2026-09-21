// frontend/src/js/features/analysis/controller.js

/**
 * Оркестрация пользовательского запуска анализа.
 *
 * Модуль связывает form, Analysis API, polling, modal и result view,
 * но не занимается поиском DOM-элементов.
 */

import {
  ApiError,
  cancelAnalysis,
  getAnalysisResult,
  getAnalysisVisualization,
  submitAnalysis,
  submitProjectContextPreflight,
} from "./api.js";

import {
  statusLabel,
} from "./labels.js";

import {
  appendAnnotatedPdfDownload,
} from "./pdf-export.js";

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
  let activeJobId = null;

  let cancellationRequested = false;


  function progressDescription(
    payload,
  ) {
    const progress = payload.progress;

    if (!progress) {
      return statusLabel(
        payload.status,
      );
    }

    if (
      progress.queue_position !== null
      && progress.queue_position !== undefined
    ) {
      return (
        `${progress.message} `
        + `Вы ${progress.queue_position}-й в очереди.`
      );
    }

    if (
      Number(progress.current) > 0
      && Number(progress.total) > 0
      && payload.status === "processing"
    ) {
      return (
        `${progress.message} `
        + `Этап ${progress.current} из ${progress.total}.`
      );
    }

    return progress.message;
  }


  function renderProgress(
    jobId,
    payload,
    elapsedSeconds,
  ) {
    if (
      cancellationRequested
      && ![
        "cancelled",
        "completed",
        "failed",
      ].includes(
        payload.status,
      )
    ) {
      const description = (
        "Останавливаю анализ… "
        + "Текущий этап завершится безопасно."
      );

      modal.show(
        `${description} Прошло ${elapsedSeconds} сек.`,
      );

      resultView.show(
        `Задание: ${jobId}\n`
        + "Статус: Отмена запрошена\n"
        + `Сейчас: ${description}\n`
        + `Прошло: ${elapsedSeconds} сек.`,
      );

      return;
    }

    const description = progressDescription(
      payload,
    );

    const status = statusLabel(
      payload.status,
    );

    modal.show(
      `${description} Прошло ${elapsedSeconds} сек.`,
    );

    const progress = payload.progress;

    const queueLine = (
      progress?.queue_position
        ? `\nМесто в очереди: ${progress.queue_position}`
        : ""
    );

    const stageLine = (
      progress
      && Number(progress.current) > 0
      && Number(progress.total) > 0
        ? (
          `\nЭтап: ${progress.current}`
          + ` из ${progress.total}`
        )
        : ""
    );

    resultView.show(
      `Задание: ${jobId}\n`
      + `Статус: ${status}\n`
      + `Сейчас: ${description}`
      + stageLine
      + queueLine
      + `\nПопытка worker: ${payload.attempt_count ?? 0}\n`
      + `Прошло: ${elapsedSeconds} сек.`,
    );
  }


  function cancellationErrorMessage(
    error,
  ) {
    if (error instanceof ApiError) {
      return error.detail;
    }

    if (error instanceof Error) {
      return error.message;
    }

    return String(
      error,
    );
  }


  async function cancelActiveAnalysis(
    jobId,
  ) {
    if (
      !jobId
      || jobId !== activeJobId
      || cancellationRequested
    ) {
      return;
    }

    try {
      const response = await cancelAnalysis(
        jobId,
      );

      if (jobId !== activeJobId) {
        return;
      }

      if (response.status !== "cancelled") {
        throw new Error(
          "API Gateway не подтвердил отмену анализа.",
        );
      }

      cancellationRequested = true;

      modal.show(
        "Отмена запрошена. "
        + "Текущий этап завершится безопасно…",
      );

      resultView.show(
        `Задание: ${jobId}\n`
        + "Статус: Отмена запрошена\n"
        + "Ожидаем остановки analysis workflow.",
      );

    } catch (error) {
      if (jobId !== activeJobId) {
        return;
      }

      modal.setCancelling(
        false,
      );

      if (
        error instanceof ApiError
        && error.status === 409
      ) {
        modal.show(
          "Анализ уже завершает работу. "
          + "Проверяем итоговый статус…",
        );

        return;
      }

      const message = cancellationErrorMessage(
        error,
      );

      modal.show(
        "Не удалось отправить запрос отмены. "
        + "Анализ продолжает выполняться.",
      );

      resultView.show(
        `Задание: ${jobId}\n`
        + "Не удалось отменить анализ.\n"
        + `${message}\n`
        + "Сам анализ продолжает выполняться.",
      );
    }
  }


  async function ensureProjectContextReady() {
    if (
      analysisForm.isProjectContextConfirmationPending()
    ) {
      resultView.show(
        "Проверьте предупреждение над диапазоном ПЗ "
        + "и выберите, изменить страницы или продолжить анализ.",
      );

      return false;
    }

    if (
      !analysisForm.needsProjectContextPreflight()
    ) {
      return true;
    }

    modal.show(
      "Проверяем выбранный диапазон ПЗ и готовим контекст проекта…",
    );

    resultView.show(
      "Проверяем страницы пояснительной записки. "
      + "Основной анализ ещё не запущен.",
    );

    try {
      const preflight = (
        await submitProjectContextPreflight(
          analysisForm.toProjectContextPreflightFormData(),
        )
      );

      const accepted = (
        analysisForm.applyProjectContextPreflight(
          preflight,
        )
      );

      if (!accepted) {
        resultView.show(
          "Автоматическая проверка ПЗ нашла спорные страницы. "
          + "Подтвердите диапазон или измените его в форме.",
        );

        return false;
      }

      return true;

    } catch (error) {
      if (
        error instanceof ApiError
        && error.status === 422
      ) {
        analysisForm.showProjectContextValidationError(
          error.detail,
        );

        resultView.show(
          "Проверьте диапазон пояснительной записки "
          + "и повторите запуск.",
        );

        return false;
      }

      throw error;
    }
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

    activeJobId = null;

    cancellationRequested = false;

    modal.clearJobId();

    try {
      const projectContextReady = (
        await ensureProjectContextReady()
      );

      if (!projectContextReady) {
        return;
      }

      modal.show(
        "Документы загружаются в API Gateway…",
      );

      resultView.show(
        "Отправляем документы в API Gateway…",
      );

      const accepted = await submitAnalysis(
        analysisForm.toFormData(),
      );

      const jobId = accepted.job_id;

      if (!jobId) {
        throw new Error(
          "API Gateway не вернул job_id.",
        );
      }

      activeJobId = jobId;

      modal.setJobId(
        jobId,
      );

      resultView.show(
        `Задание: ${jobId}\n`
        + `Статус: ${statusLabel(accepted.status)}`,
      );

      const finalStatus = await waitForAnalysis(
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

      if (finalStatus.status === "cancelled") {
        resultView.show(
          `Задание: ${jobId}\n`
          + `Статус: ${statusLabel(finalStatus.status)}\n`
          + "Анализ остановлен по запросу пользователя.",
        );

        return;
      }

      modal.show(
        "Анализ завершён. Загружаем результат…",
      );

      const payload = await getAnalysisResult(
        jobId,
      );

      let visualization = null;

      if (
        payload.source_mode === "pdf_only"
        || payload.source_mode === "pdf_cad"
      ) {
        modal.show(
          "Анализ завершён. Готовим визуализацию замечаний…",
        );

        try {
          visualization = (
            await getAnalysisVisualization(
              jobId,
            )
          );

        } catch (visualizationError) {
          visualization = {
            pages: [],
            error: (
              visualizationError instanceof Error
                ? visualizationError.message
                : String(
                  visualizationError,
                )
            ),
          };
        }
      }

      const report = renderAnalysisReport(
        payload,
        {
          jobId,
          visualization,
        },
      );

      appendAnnotatedPdfDownload(
        report,
        {
          jobId,
          payload,
        },
      );

      resultView.showReport(
        report,
      );

    } catch (error) {
      resultView.showError(
        error,
      );

    } finally {
      activeJobId = null;

      cancellationRequested = false;

      modal.setCancelling(
        false,
      );

      modal.hide();
    }
  }


  modal.setCancelHandler(
    cancelActiveAnalysis,
  );


  return {
    submit,
  };
}