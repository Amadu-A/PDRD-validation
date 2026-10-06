// frontend/src/js/features/review/rejection-reasons.js

/** Стабильные категории отказа; русские подписи общие для Review и Experience. */
export const REJECTION_REASONS = Object.freeze({
  false_positive: "Замечание ошибочное",
  duplicate: "Дублируется",
  misunderstood_drawing: "Неверно определён объект",
  wrong_location: "Неверно определено место",
  wrong_normative_basis: "Неверно применён норматив",
  not_applicable: "Требование неприменимо",
  other: "Другая причина",
});

/** Проверяет новое объяснение; старые серверные записи могут не иметь причины. */
export function rejectionFeedback(reasonCategory, comment = "", { legacy = false } = {}) {
  if (typeof comment !== "string" || comment.length > 2000) {
    throw new Error("Комментарий не должен превышать 2000 символов.");
  }
  if (legacy && reasonCategory == null && !comment.trim()) return { reasonCategory: null, comment: "" };
  if (typeof reasonCategory !== "string" || !Object.hasOwn(REJECTION_REASONS, reasonCategory)) {
    throw new Error("Выберите причину отклонения замечания.");
  }
  return { reasonCategory, comment: comment.trim() };
}
