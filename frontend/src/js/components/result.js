// frontend/src/js/components/result.js

/**
 * Управляет контейнером результата анализа.
 *
 * Progress/error показываются как обычный текст.
 * Финальный отчёт передаётся как безопасно построенный DOM fragment.
 */

export function createResultView(
  element,
) {
  function show(
    text,
  ) {
    const node = document.createElement(
      "p",
    );

    node.className = (
      "analysis-result__message"
    );

    node.textContent = String(
      text,
    );

    element.replaceChildren(
      node,
    );
  }


  function showReport(
    report,
  ) {
    element.replaceChildren(
      report,
    );
  }


  function showError(
    error,
  ) {
    const message = (
      error instanceof Error
        ? error.message
        : String(
          error,
        )
    );

    const node = document.createElement(
      "p",
    );

    node.className = (
      "analysis-result__message "
      + "analysis-result__message--error"
    );

    node.textContent = (
      `Ошибка:\n${message}`
    );

    element.replaceChildren(
      node,
    );
  }


  return {
    show,
    showReport,
    showError,
  };
}