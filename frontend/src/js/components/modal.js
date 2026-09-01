// frontend/src/js/components/modal.js

/**
 * Управляет модальным состоянием длительной операции.
 */

export function createModal({
  modalElement,
  textElement,
  submitButton,
}) {
  function show(
    text = "Идёт анализ документа…",
  ) {
    textElement.textContent = text;

    modalElement.classList.remove(
      "hidden",
    );

    modalElement.setAttribute(
      "aria-hidden",
      "false",
    );

    submitButton.disabled = true;
  }


  function hide() {
    modalElement.classList.add(
      "hidden",
    );

    modalElement.setAttribute(
      "aria-hidden",
      "true",
    );

    submitButton.disabled = false;
  }


  return {
    show,
    hide,
  };
}