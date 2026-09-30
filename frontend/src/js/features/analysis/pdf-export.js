// frontend/src/js/features/analysis/pdf-export.js

/**
 * Разделяет автоматический PDF и утверждённый PDF после Human Review.
 * Итоговую кнопку подключает отдельный модуль серверного Review.
 */

function createElement(
  tagName,
  className = "",
  text = null,
) {
  const element = document.createElement(
    tagName,
  );

  if (className) {
    element.className = className;
  }

  if (
    text !== null
    && text !== undefined
  ) {
    element.textContent = String(
      text,
    );
  }

  return element;
}


/**
 * Выводит исходный PDF и кнопку итогового PDF до подключения серверного Review.
 */
export function appendAnnotatedPdfDownload(
  parent,
  {
    jobId,
    payload,
  },
) {
  if (
    !jobId
    || payload?.status !== "completed"
    || ![
      "pdf_only",
      "pdf_cad",
    ].includes(
      payload?.source_mode,
    )
  ) {
    return;
  }

  const section = createElement(
    "section",
    "analysis-export",
  );

  section.append(
    createElement(
      "h3",
      "analysis-export__title",
      "PDF с замечаниями",
    ),
  );

  section.append(
    createElement(
      "p",
      "analysis-export__description",
      (
        "Существующий автоматический PDF не учитывает Wise, Bad, Edited "
        + "и ручные Gold-замечания. Он доступен только как исходная версия."
      ),
    ),
  );

  const link = createElement(
    "a",
    "analysis-export__download",
    "Скачать PDF с аннотациями — автоматический, без решений пользователя",
  );

  link.href = (
    "/api/v1/analyses/"
    + `${encodeURIComponent(jobId)}`
    + "/annotated-pdf"
  );

  link.download = "";
  link.dataset.analysisPdfOriginal = "";

  section.append(
    link,
  );

  const finalButton = createElement(
    "button",
    "analysis-export__pending",
    "Итоговый PDF после Human Review",
  );

  finalButton.type = "button";
  finalButton.disabled = true;
  finalButton.dataset.analysisPdfReviewed = "";

  finalButton.title = (
    "Сначала примите или отклоните все замечания. "
    + "Итоговый PDF включает принятые замечания с проверенной областью на листах "
    + "и в текстовом списке."
  );

  section.append(
    finalButton,
  );

  parent.append(
    section,
  );
}
