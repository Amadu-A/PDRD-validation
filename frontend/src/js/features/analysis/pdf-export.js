// frontend/src/js/features/analysis/pdf-export.js

/**
 * Добавляет lazy-download annotated PDF в самый низ результата анализа.
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
 * Добавляет ссылку скачивания только для completed анализа с PDF.
 *
 * @param {DocumentFragment|HTMLElement} parent Target container.
 * @param {object} options Export options.
 * @param {string|null} options.jobId Analysis job id.
 * @param {object} options.payload Completed analysis payload.
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
        "Скачать исходный PDF с интерактивными "
        + "аннотациями на листах и полным "
        + "текстовым отчётом в конце файла."
      ),
    ),
  );

  const link = createElement(
    "a",
    "analysis-export__download",
    "Скачать PDF с аннотациями",
  );

  link.href = (
    "/api/v1/analyses/"
    + `${encodeURIComponent(jobId)}`
    + "/annotated-pdf"
  );

  link.download = "";

  section.append(
    link,
  );

  parent.append(
    section,
  );
}