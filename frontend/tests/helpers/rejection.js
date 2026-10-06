// frontend/tests/helpers/rejection.js

/** Подтверждает причину отказа через форму, которую использует настоящий UI. */
export function submitRejection(root, reason = "false_positive", comment = "") {
  const dialog = root.querySelector("[data-review-rejection-dialog]");
  dialog.querySelector("[data-review-rejection-reason]").value = reason;
  dialog.querySelector("[data-review-rejection-comment]").value = comment;
  dialog.children[0].dispatch("submit", { preventDefault() {} });
  return dialog;
}
