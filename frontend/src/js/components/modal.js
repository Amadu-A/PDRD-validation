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


  function setJobId(
    value,
  ) {
    jobId = value;

    jobIdElement.textContent = value;

    jobElement.hidden = false;

    copyStatusElement.textContent = "";
  }


  function clearJobId() {
    jobId = null;

    jobIdElement.textContent = "";

    jobElement.hidden = true;

    copyStatusElement.textContent = "";
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


  clearJobId();


  return {
    show,
    hide,
    setJobId,
    clearJobId,
  };
}