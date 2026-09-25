// frontend/src/js/features/review/controls.js

/**
 * Доступные DOM-контролы ручной проверки одного finding.
 * Не содержит состояния и не обращается к API.
 */

function createAction(label, icon, action, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `review-controls__button review-controls__button--${action}`;
  button.dataset.reviewAction = action;
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", "false");
  button.title = label;

  const symbol = document.createElement("span");
  symbol.className = "review-controls__icon";
  symbol.setAttribute("aria-hidden", "true");
  symbol.textContent = icon;
  button.append(symbol);
  button.addEventListener("click", handler);
  return button;
}


/** Возвращает набор кнопок и узлов для независимого обновления. */
export function createReviewControls({ onAccept, onReject, onEdit }) {
  const element = document.createElement("div");
  element.className = "review-controls";
  element.dataset.reviewControls = "";
  element.setAttribute("role", "group");
  element.setAttribute("aria-label", "Решение по замечанию");

  const accept = createAction("Принять замечание", "✓", "accept", onAccept);
  const reject = createAction("Отклонить замечание", "✕", "reject", onReject);
  const edit = createAction("Редактировать замечание", "✎", "edit", onEdit);

  const status = document.createElement("span");
  status.className = "review-controls__status";
  status.dataset.reviewStatus = "";
  status.textContent = "Ожидает решения";

  element.append(accept, reject, edit, status);
  return { element, accept, reject, edit, status };
}