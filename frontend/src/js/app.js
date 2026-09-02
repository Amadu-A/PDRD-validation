// frontend/src/js/app.js

/**
 * Composition root браузерного приложения PDRD Validation.
 *
 * Модуль получает обязательные DOM-зависимости через общий helper,
 * собирает компоненты формы, результата и модального состояния, после чего
 * связывает их с публичным Analysis API Gateway. Бизнес-валидация формы,
 * polling и форматирование результата остаются в feature-модулях.
 */

import {
  createModal,
} from "./components/modal.js";

import {
  createResultView,
} from "./components/result.js";

import {
  requireElement,
} from "./dom.js";

import {
  getAnalysisResult,
  submitAnalysis,
} from "./features/analysis/api.js";

import {
  createAnalysisForm,
} from "./features/analysis/form.js";

import {
  statusLabel,
} from "./features/analysis/labels.js";

import {
  waitForAnalysis,
} from "./features/analysis/polling.js";

import {
  renderAnalysisReport,
} from "./features/analysis/report.js";


const analysisFormElement = requireElement(
  "[data-analysis-form]",
);

const submitButton = requireElement(
  "[data-submit-button]",
);


const modal = createModal({
  modalElement: requireElement(
    "[data-analysis-modal]",
  ),

  textElement: requireElement(
    "[data-analysis-modal-text]",
  ),

  submitButton,
});


const resultView = createResultView(
  requireElement(
    "[data-analysis-result]",
  ),
);


const analysisForm = createAnalysisForm({
  pdfInput: requireElement(
    "[data-pdf-input]",
  ),

  cadInput: requireElement(
    "[data-cad-input]",
  ),

  pagesInput: requireElement(
    "[data-pages-input]",
  ),

  pagesHint: requireElement(
    "[data-pages-hint]",
  ),

  useExplanatoryNoteInput: requireElement(
    "[data-explanatory-note-input]",
  ),

  noteStartPageInput: requireElement(
    "[data-note-start-input]",
  ),

  noteEndPageInput: requireElement(
    "[data-note-end-input]",
  ),
});


/**
 * Показывает пользователю текущее состояние фонового analysis job.
 *
 * @param {string} jobId Идентификатор задания API Gateway.
 * @param {object} payload Последний status payload задания.
 * @param {number} elapsedSeconds Время ожидания результата в секундах.
 * @returns {void}
 */
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


/**
 * Валидирует форму, создаёт analysis job и ожидает готовый результат.
 *
 * Все HTTP-вызовы выполняются только через публичный API Gateway.
 * Модуль не обращается напрямую к n8n или внутренним application services.
 *
 * @param {SubmitEvent} event Событие отправки формы анализа.
 * @returns {Promise<void>}
 */
async function handleAnalysisSubmit(
  event,
) {
  event.preventDefault();

  const validation = analysisForm.validate();

  if (!validation.valid) {
    if (validation.message) {
      resultView.show(
        validation.message,
      );
    }

    return;
  }

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

    resultView.show(
      `Задание создано: ${jobId}\n`
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

    resultView.show(
      renderAnalysisReport(
        payload,
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


analysisForm.bind();

analysisFormElement.addEventListener(
  "submit",
  handleAnalysisSubmit,
);