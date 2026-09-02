// frontend/src/js/app.js

/**
 * Composition root браузерного приложения.
 */

import {
  createModal,
} from "./components/modal.js";

import {
  createResultView,
} from "./components/result.js";

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


const analysisFormElement = document.getElementById(
  "analysisForm",
);

const submitButton = document.getElementById(
  "submitButton",
);


const modal = createModal({
  modalElement: document.getElementById(
    "modal",
  ),
  textElement: document.getElementById(
    "modalText",
  ),
  submitButton,
});


const resultView = createResultView(
  document.getElementById(
    "result",
  ),
);


const analysisForm = createAnalysisForm({
  pdfInput: document.getElementById(
    "pdfFile",
  ),

  cadInput: document.getElementById(
    "cadFile",
  ),

  pagesInput: document.getElementById(
    "pages",
  ),

  pagesHint: document.getElementById(
    "pagesHint",
  ),

  useExplanatoryNoteInput: document.getElementById(
    "useExplanatoryNote",
  ),

  noteStartPageInput: document.getElementById(
    "noteStartPage",
  ),

  noteEndPageInput: document.getElementById(
    "noteEndPage",
  ),
});


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
      renderAnalysisReport(payload),
    );

  } catch (error) {
    resultView.showError(error);

  } finally {
    modal.hide();
  }
}


analysisForm.bind();

analysisFormElement.addEventListener(
  "submit",
  handleAnalysisSubmit,
);