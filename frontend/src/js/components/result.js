// frontend/src/js/components/result.js

/**
 * Отвечает только за вывод текста результата.
 */

export function createResultView(
  element,
) {
  function show(text) {
    element.textContent = text;
  }


  function showError(error) {
    const message = (
      error instanceof Error
        ? error.message
        : String(error)
    );

    show(
      `Ошибка:\n${message}`,
    );
  }


  return {
    show,
    showError,
  };
}