// frontend/src/js/features/experience/labels.js

/** Подписи фильтров Experience: исправление и решение остаются разными полями. */

export const DECISION_LABELS = Object.freeze({
  pending: "Ожидает решения", accepted: "Принято", rejected: "Отклонено",
});

/** Для Edited показывает решение прямо в подписи тега. */
export function experienceTagLabel(tag, decision) {
  const label = { wise: "Wise", bad: "Bad", edited: "Edited", gold: "Gold" }[tag] ?? tag;
  return tag === "edited" ? `${label} — ${DECISION_LABELS[decision] ?? decision}` : label;
}

/** Составное значение используется только в UI, не меняет хранимый тег. */
export function matchesTagFilter(item, filter) {
  if (!filter) return true;
  const [tag, decision, extra] = filter.split(":");
  return !extra && item.tag === tag && (!decision || (tag === "edited" && item.decision === decision));
}
