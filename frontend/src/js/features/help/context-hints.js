// frontend/src/js/features/help/context-hints.js

/** Нативные раскрывающиеся подсказки не изменяют состояние формы. */
import { ANALYSIS_SETTINGS } from "./settings.js";
import { helpElement } from "./view.js";

/** Добавляет по одной подсказке вне label, чтобы её раскрытие не переключало input. */
export function mountAnalysisHints(root) {
  for (const setting of ANALYSIS_SETTINGS) {
    const host = root.querySelector(`[data-setting-help="${setting.id}"]`);
    if (!host || host.dataset.helpMounted) continue;
    host.dataset.helpMounted = "true";
    const details = helpElement("details", null, "setting-help");
    const summary = helpElement("summary", "?", "setting-help__icon");
    summary.setAttribute("aria-label", `Справка: ${setting.label}`);
    const content = helpElement("div", null, "setting-help__content");
    const link = helpElement("a", "Подробнее в руководстве →");
    link.href = `/help.html#${setting.id}`;
    content.append(helpElement("p", setting.summary), helpElement("p", setting.availability), link);
    details.append(summary, content); host.append(details);
  }
}
