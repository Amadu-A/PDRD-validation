// frontend/src/js/dom.js

/**
 * Общие безопасные helpers доступа к DOM.
 *
 * Модуль отделяет технический поиск обязательных элементов от composition
 * root и feature-логики. Вызовы используют стабильные data-* selectors,
 * чтобы визуальный BEM-refactoring не ломал JavaScript.
 */


/**
 * Возвращает обязательный DOM-элемент по CSS selector.
 *
 * Отсутствие элемента считается ошибкой frontend-разметки: приложение
 * завершает bootstrap с понятным сообщением вместо последующей ошибки
 * обращения к null.
 *
 * @param {string} selector CSS selector обязательного элемента.
 * @returns {Element} Найденный DOM-элемент.
 * @throws {Error} Если обязательный элемент отсутствует.
 */
export function requireElement(
  selector,
) {
  const element = document.querySelector(
    selector,
  );

  if (!element) {
    throw new Error(
      `Не найден обязательный DOM-элемент: ${selector}`,
    );
  }

  return element;
}