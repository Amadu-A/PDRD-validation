// frontend/src/js/features/review/state.js

/**
 * Чистая модель локального review. Происхождение находки и решение
 * пользователя независимы: edited никогда не становится gold.
 */

export const REVIEW_DECISIONS = Object.freeze({
  PENDING: "pending",
  ACCEPTED: "accepted",
  REJECTED: "rejected",
});

export const REVIEW_ORIGINS = Object.freeze({
  VLM: "vlm",
  MANUAL: "manual",
});

function validatedText(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    throw new Error("Текст замечания не может быть пустым.");
  }
  if (text.length > 10000) {
    throw new Error("Текст замечания не должен превышать 10000 символов.");
  }
  return text;
}

function validatedNormative(value) {
  const text = String(value ?? "").trim();
  if (text.length > 2000) {
    throw new Error("Нормативное основание не должно превышать 2000 символов.");
  }
  return text;
}

function experienceTag(entry) {
  if (entry.origin === REVIEW_ORIGINS.MANUAL) {
    return "gold";
  }
  if (entry.edited) {
    return "edited";
  }
  if (entry.decision === REVIEW_DECISIONS.ACCEPTED) {
    return "wise";
  }
  if (entry.decision === REVIEW_DECISIONS.REJECTED) {
    return "bad";
  }
  return null;
}

/** Создаёт независимые данные одного отображаемого отчёта. */
export function createReviewState() {
  const entries = new Map();

  function requireEntry(findingId) {
    const entry = entries.get(findingId);
    if (!entry) {
      throw new Error(`Неизвестное замечание: ${findingId}`);
    }
    return entry;
  }

  function get(findingId) {
    const entry = requireEntry(findingId);
    return { ...entry, experienceTag: experienceTag(entry) };
  }

  function register(findingId, text, {
    origin = REVIEW_ORIGINS.VLM,
    normativeSection = "",
  } = {}) {
    const id = String(findingId ?? "").trim();
    if (!id || !Object.values(REVIEW_ORIGINS).includes(origin)) {
      throw new Error("Неверный ID или источник замечания.");
    }

    const normalizedText = String(text ?? "").trim();
    if (!normalizedText) {
      throw new Error("Текст замечания не может быть пустым.");
    }

    const normalizedNormative = validatedNormative(normativeSection);

    if (entries.has(id)) {
      const existing = requireEntry(id);
      if (existing.origin !== origin) {
        throw new Error("Конфликт источников замечания с одинаковым ID.");
      }
      return get(id);
    }

    entries.set(id, {
      findingId: id,
      origin,
      originalText: normalizedText,
      originalNormativeSection: normalizedNormative,
      text: normalizedText,
      normativeSection: normalizedNormative,
      decision: REVIEW_DECISIONS.PENDING,
      edited: false,
      revision: 0,
    });

    return get(id);
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

  function edit(findingId, text, options = {}) {
    const nextText = validatedText(text);
    const entry = requireEntry(findingId);

    const nextNormative = Object.hasOwn(options, "normativeSection")
      ? validatedNormative(options.normativeSection)
      : entry.normativeSection;

    if (entry.text !== nextText || entry.normativeSection !== nextNormative) {
      entry.text = nextText;
      entry.normativeSection = nextNormative;

      entry.edited = entry.text !== entry.originalText
        || nextNormative !== entry.originalNormativeSection;

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

  /** Сохранённая правка области требует нового решения по замечанию. */
  function invalidate(findingId) {
    const entry = requireEntry(findingId);
    entry.decision = REVIEW_DECISIONS.PENDING;
    entry.revision += 1;
    return get(findingId);
  }

  /** Undo решения сохраняет запись и увеличивает ревизию истории. */
  function restoreDecision(findingId, decision) {
    if (!Object.values(REVIEW_DECISIONS).includes(decision)) {
      throw new Error("Недопустимое решение пользователя.");
    }
    const entry = requireEntry(findingId);
    if (entry.decision !== decision) {
      entry.decision = decision;
      entry.revision += 1;
    }
    return get(findingId);
  }

  /** Совместимость локального Gold API; автоматические области используют invalidate. */
  function invalidateManual(findingId) {
    if (requireEntry(findingId).origin !== REVIEW_ORIGINS.MANUAL) {
      throw new Error("Эта операция доступна только для Gold-замечаний.");
    }
    return invalidate(findingId);
  }

  /** Отменяет создание локального Gold; отклонение запись не удаляет. */
  function removeManual(findingId) {
    const entry = get(findingId);
    if (entry.origin !== REVIEW_ORIGINS.MANUAL) {
      throw new Error("Удалять можно только локальные Gold-замечания.");
    }
    entries.delete(findingId);
    return entry;
  }

  /** Восстанавливает сохранённый локальный снимок при отмене удаления. */
  function restoreManual(snapshot) {
    if (snapshot.origin !== REVIEW_ORIGINS.MANUAL || entries.has(snapshot.findingId)) {
      throw new Error("Нельзя восстановить это Gold-замечание.");
    }
    const entry = { ...snapshot };
    entry.text = validatedText(entry.text);
    entry.originalText = validatedText(entry.originalText);
    entry.normativeSection = validatedNormative(entry.normativeSection);
    delete entry.experienceTag;
    entries.set(entry.findingId, entry);
    return get(entry.findingId);
  }

  return {
    register, get, decide, edit, summary,
    invalidate, invalidateManual, restoreDecision, removeManual, restoreManual,
    snapshot: () => [...entries.keys()].map(get),
  };
}
