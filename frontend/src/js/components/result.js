// frontend/src/js/components/result.js

/**
 * Управляет контейнером результата анализа.
 *
 * Ход проверки и ошибки показываются как обычный текст.
 * Финальный отчёт передаётся как безопасно построенный DOM fragment.
 * При завершении рендера вызывает подключённый обработчик.
 */

export function createResultView(
  element,
  { onReportRendered = () => {}, onReportCleared = () => {} } = {},
) {
  function show(
    text,
  ) {
    onReportCleared();
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
    context = {},
  ) {
    element.replaceChildren(
      report,
    );
    onReportRendered(element, context);
  }


  function showError(
    error,
  ) {
    onReportCleared();
    const message = (
      typeof error?.detail === "string"
        ? error.detail
        : error instanceof Error
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
