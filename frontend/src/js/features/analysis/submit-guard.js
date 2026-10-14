// frontend/src/js/features/analysis/submit-guard.js

/** Объединяет причины блокировки запуска: раздел, подготовка ТЗ и активный анализ. */
const blockers = new WeakMap();

/** Обновляет одну причину, сохраняя остальные блокировки и подсказку при наведении. */
export function setAnalysisSubmitBlock(button, reason, message = "") {
  if (!button) return;
  const state = blockers.get(button) ?? new Map();
  if (message) state.set(reason, message);
  else state.delete(reason);
  blockers.set(button, state);
  const description = [...state.values()].join(" ");
  button.disabled = state.size > 0;
  button.title = description;
  const wrapper = button.closest?.("[data-analysis-submit-guard]") ?? button.parentElement;
  if (wrapper) {
    wrapper.title = description;
    if (wrapper.matches?.("[data-analysis-submit-guard]")) {
      wrapper.tabIndex = button.disabled ? 0 : -1;
      wrapper.setAttribute("aria-label", description || "Запуск анализа");
    }
  }
}
