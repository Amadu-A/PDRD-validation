// frontend/src/js/components/modal.js

/**
 * Компонент модального состояния длительной операции.
 */


async function copyText(
  value,
) {
  if (
    navigator.clipboard
    && typeof navigator.clipboard.writeText
    === "function"
  ) {
    try {
      await navigator.clipboard.writeText(
        value,
      );

      return;

    } catch {
      // Для HTTP-host fallback ниже.
    }
  }

  const temporary = document.createElement(
    "textarea",
  );

  temporary.value = value;

  temporary.setAttribute(
    "readonly",
    "",
  );

  temporary.style.position = "fixed";

  temporary.style.opacity = "0";

  document.body.append(
    temporary,
  );

  temporary.select();

  const copied = document.execCommand(
    "copy",
  );

  temporary.remove();

  if (!copied) {
    throw new Error(
      "Не удалось скопировать номер задания.",
    );
  }
}


/**
 * Создаёт контроллер modal анализа.
 */
export function createModal({
  modalElement,
  textElement,
  submitButton,
  jobElement,
  jobIdElement,
  copyButton,
  copyStatusElement,
}) {
  let jobId = null;

  let cancelHandler = null;

  const cancelButton = document.createElement(
    "button",
  );

  cancelButton.type = "button";

  cancelButton.className = (
    "analysis-modal__cancel-button"
  );

  cancelButton.textContent = (
    "Отменить анализ"
  );

  cancelButton.hidden = true;

  jobElement.append(
    cancelButton,
  );


  function show(
    text = "Идёт анализ документа…",
  ) {
    textElement.textContent = text;

    modalElement.classList.remove(
      "is-hidden",
    );

    modalElement.setAttribute(
      "aria-hidden",
      "false",
    );

    submitButton.disabled = true;
  }


  function hide() {
    modalElement.classList.add(
      "is-hidden",
    );

    modalElement.setAttribute(
      "aria-hidden",
      "true",
    );

    submitButton.disabled = false;
  }


  function setCancelling(
    value,
  ) {
    const cancelling = Boolean(
      value,
    );

    cancelButton.disabled = cancelling;

    cancelButton.textContent = (
      cancelling
        ? "Отменяю анализ…"
        : "Отменить анализ"
    );
  }


  function setCancelHandler(
    handler,
  ) {
    cancelHandler = handler;
  }


  function setJobId(
    value,
  ) {
    jobId = value;

    jobIdElement.textContent = value;

    jobElement.hidden = false;

    copyStatusElement.textContent = "";

    cancelButton.hidden = false;

    setCancelling(
      false,
    );
  }


  function clearJobId() {
    jobId = null;

    jobIdElement.textContent = "";

    jobElement.hidden = true;

    copyStatusElement.textContent = "";

    cancelButton.hidden = true;

    setCancelling(
      false,
    );
  }


  copyButton.addEventListener(
    "click",
    async () => {
      if (!jobId) {
        return;
      }

      try {
        await copyText(
          jobId,
        );

        copyStatusElement.textContent = (
          "Скопировано"
        );

      } catch (error) {
        copyStatusElement.textContent = (
          error instanceof Error
            ? error.message
            : String(error)
        );
      }
    },
  );


  cancelButton.addEventListener(
    "click",
    async () => {
      if (
        !jobId
        || cancelButton.disabled
        || typeof cancelHandler !== "function"
      ) {
        return;
      }

      setCancelling(
        true,
      );

      copyStatusElement.textContent = "";

      try {
        await cancelHandler(
          jobId,
        );

      } catch (error) {
        setCancelling(
          false,
        );

        console.error(
          "Не удалось обработать отмену анализа.",
          error,
        );
      }
    },
  );


  clearJobId();


  return {
    show,
    hide,
    setJobId,
    clearJobId,
    setCancelHandler,
    setCancelling,
  };
}