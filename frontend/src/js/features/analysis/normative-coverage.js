// frontend/src/js/features/analysis/normative-coverage.js

/** Показывает ограничения сохранённого N-first, не затрагивая карточки и геометрию Review. */
export function normativeCoverageText(payload) {
  const counts = payload.normative_evaluation_counts;
  const statistics = counts ? ` Требований в корпусе: ${counts.requirements_total ?? 0}; выбрано: ${counts.requirements_selected ?? 0}; поддержано алгоритмом: ${counts.requirements_supported ?? 0}; не поддержано: ${counts.requirements_unsupported ?? 0}; проверено по существу: ${counts.requirements_evaluated ?? 0}; применимость неизвестна: ${counts.applicability_unknown ?? 0}.` : "";
  if (payload.normative_coverage === "complete") {
    return "Атомарный нормативный корпус получен полностью. Поддержка правил, применимость к проекту и нормативный статус оцениваются отдельно." + statistics;
  }
  if (payload.normative_coverage === "incomplete") {
    return "Нормативная проверка неполная: атомарный корпус отсутствует или содержит непрочитанные страницы. Выполненные проверки по доступным источникам сохранены." + statistics;
  }
  return "Полнота нормативной проверки неизвестна: в сохранённом результате нет данных о готовности атомарного корпуса.";
}
