// frontend/src/js/features/review/state.js

/**
 * Чистая модель локальных решений по замечаниям.
 * Не выполняет HTTP-запросы и не объявляет решения сохранёнными.
 */

export const REVIEW_DECISIONS = Object.freeze({
  PENDING: "pending",
  ACCEPTED: "accepted",
  REJECTED: "rejected",
});


/** Создаёт независимое состояние одного отображаемого отчёта. */
export function createReviewState() {
  const entries = new Map();

  function requireEntry(findingId) {
    const entry = entries.get(findingId);
    if (!entry) {
      throw new Error(`Неизвестное замечание: ${findingId}`);
    }
    return entry;
  }

  function register(findingId, text) {
    const id = String(findingId ?? "").trim();
    const initialText = String(text ?? "").trim();
    if (!id || !initialText) {
      throw new Error("Для замечания обязательны ID и текст.");
    }
    if (!entries.has(id)) {
      entries.set(id, {
        findingId: id,
        originalText: initialText,
        text: initialText,
        decision: REVIEW_DECISIONS.PENDING,
        edited: false,
        revision: 0,
      });
    }
    return get(id);
  }

  function get(findingId) {
    return { ...requireEntry(findingId) };
  }

  function decide(findingId, decision) {
    if (![REVIEW_DECISIONS.ACCEPTED, REVIEW_DECISIONS.REJECTED]
      .includes(decision)) {
      throw new Error("Недопустимое решение пользователя.");
    }
    const entry = requireEntry(findingId);
    if (entry.decision !== decision) {
      entry.decision = decision;
      entry.revision += 1;
    }
    return get(findingId);
  }

  function edit(findingId, text) {
    const nextText = String(text ?? "").trim();
    if (!nextText) {
      throw new Error("Текст замечания не может быть пустым.");
    }
    if (nextText.length > 10000) {
      throw new Error("Текст замечания не должен превышать 10000 символов.");
    }
    const entry = requireEntry(findingId);
    if (entry.text !== nextText) {
      entry.text = nextText;
      entry.edited = entry.text !== entry.originalText;
      entry.decision = REVIEW_DECISIONS.PENDING;
      entry.revision += 1;
    }
    return get(findingId);
  }

  function summary() {
    const values = [...entries.values()];
    return {
      total: values.length,
      pending: values.filter((item) => (
        item.decision === REVIEW_DECISIONS.PENDING
      )).length,
      accepted: values.filter((item) => (
        item.decision === REVIEW_DECISIONS.ACCEPTED
      )).length,
      rejected: values.filter((item) => (
        item.decision === REVIEW_DECISIONS.REJECTED
      )).length,
    };
  }

  return { register, get, decide, edit, summary };
}