// frontend/src/js/features/normative/readiness.js

/** Отдельные признаки готовности поиска, атомарного корпуса и правового статуса. */
export function documentReadinessText(item) {
  if (!item) return "N-first: состояние неизвестно. Нормативный статус: не подтверждён.";
  const labels = { not_indexed: "не подготовлен", queued: "в очереди", building: "строится", ready: "готов", failed: "ошибка", corrupt: "повреждён" };
  const state = labels[item.atomic_state] || "неизвестно";
  const coverage = item.atomic_state === "ready" && item.source_coverage !== "complete" ? "; извлечение неполное" : "";
  const legal = item.status_evidence_count ? "есть свидетельства; применимость проверяется по пунктам" : "не подтверждён";
  return `N-first: ${state}${coverage}. Атомов: ${item.requirements || 0}; простая числовая форма (нижняя граница поддержки): ${item.requirements_supported || 0}. Нормативный статус: ${legal}.`;
}

export function sectionReadinessText(report) {
  if (!report || report.error) return "Готовность N-first не получена. Полнота нормативной проверки не подтверждена.";
  const rows = report.documents || [];
  const complete = rows.filter((item) => item.atomic_state === "ready" && item.source_coverage === "complete").length;
  const ready = rows.filter((item) => item.search_state === "ready").length;
  return `Поиск: ${ready}/${rows.length}. Атомарный корпус N-first: ${complete}/${rows.length}. ${complete === rows.length && rows.length ? "Поддержка правил и нормативный статус проверяются отдельно." : "Нормативная проверка будет неполной: корпус не подготовлен полностью."}`;
}
