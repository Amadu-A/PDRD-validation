// frontend/src/js/components/modal.js

/**
 * Компонент модального состояния длительной операции.
 *
 * Компонент отвечает только за видимость modal, текст текущего состояния
 * и временную блокировку submit-кнопки. Он не знает ничего о polling,
 * Analysis API или бизнес-статусах задания.
 */


/**
 * Создаёт контроллер модального состояния анализа.
 *
 * @param {object} dependencies DOM-зависимости компонента.
 * @param {Element} dependencies.modalElement Корневой modal-элемент.
 * @param {Element} dependencies.textElement Элемент текста состояния.
 * @param {Element} dependencies.submitButton Кнопка запуска анализа.
 * @returns {{show: Function, hide: Function}} Контроллер modal.
 */
export function createModal({
  modalElement,
  textElement,
  submitButton,
}) {
  /**
   * Показывает modal и блокирует повторный запуск анализа.
   *
   * @param {string} text Текст текущего состояния операции.
   * @returns {void}
   */
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


  /**
   * Скрывает modal и снова разрешает запуск анализа.
   *
   * @returns {void}
   */
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


  return {
    show,
    hide,
  };
}