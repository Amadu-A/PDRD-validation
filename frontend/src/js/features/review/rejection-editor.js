// frontend/src/js/features/review/rejection-editor.js

/** Доступный диалог отказа; состояние и сохранение остаются в контроллере Review. */
import { REJECTION_REASONS, rejectionFeedback } from "./rejection-reasons.js";

function element(tag, text = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

/** Не изменяет решение до подтверждения причины; отмена и Escape оставляют данные прежними. */
export function createRejectionEditor(onSave) {
  const dialog = element("dialog");
  dialog.className = "review-editor";
  dialog.dataset.reviewRejectionDialog = "";
  dialog.setAttribute("aria-labelledby", "reviewRejectionTitle");
  const form = element("form");
  const title = element("h3", "Отклонить замечание");
  title.className = "review-editor__title";
  title.id = "reviewRejectionTitle";
  const reasonLabel = element("label", "Причина:");
  reasonLabel.className = "review-editor__label";
  reasonLabel.htmlFor = "reviewRejectionReason";
  const reason = element("select");
  reason.className = "review-editor__textarea";
  reason.id = reasonLabel.htmlFor;
  reason.required = true;
  reason.dataset.reviewRejectionReason = "";
  const placeholder = element("option", "Выберите причину");
  placeholder.value = "";
  reason.append(placeholder);
  for (const [value, label] of Object.entries(REJECTION_REASONS)) {
    const option = element("option", label);
    option.value = value;
    reason.append(option);
  }
  const commentLabel = element("label", "Комментарий (необязательно):");
  commentLabel.className = "review-editor__label";
  commentLabel.htmlFor = "reviewRejectionComment";
  const comment = element("textarea");
  comment.className = "review-editor__textarea";
  comment.id = commentLabel.htmlFor;
  comment.maxLength = 2000;
  comment.rows = 4;
  comment.dataset.reviewRejectionComment = "";
  const error = element("p");
  error.className = "review-editor__error";
  error.setAttribute("role", "alert");
  const actions = element("div");
  actions.className = "review-editor__actions";
  const cancel = element("button", "Отмена");
  cancel.type = "button";
  cancel.className = "review-editor__button";
  cancel.dataset.reviewRejectionCancel = "";
  cancel.addEventListener("click", () => dialog.close());
  const save = element("button", "Отклонить замечание");
  save.type = "submit";
  save.className = "review-editor__button review-editor__button--save";
  save.dataset.reviewRejectionSave = "";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const feedback = rejectionFeedback(reason.value, comment.value);
      onSave(feedback);
      dialog.close();
    } catch (problem) { error.textContent = problem.message; }
  });
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); dialog.close(); });
  actions.append(cancel, save);
  form.append(title, reasonLabel, reason, commentLabel, comment, error, actions);
  dialog.append(form);
  return {
    dialog,
    open(entry) {
      reason.value = entry.reasonCategory ?? "";
      comment.value = entry.comment ?? "";
      error.textContent = "";
      dialog.showModal();
      reason.focus();
    },
  };
}
