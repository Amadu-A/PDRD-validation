// frontend/src/js/features/help/navigation.js

/** Единая публичная ссылка справки в существующей общей навигации. */
import { helpElement } from "./view.js";

/** Дополняет навигацию одинаково для гостя и сотрудника, без повторных ссылок. */
export function mountHelpNavigation(root) {
  const nav = root.querySelector('nav[aria-label="Основная навигация"]');
  if (!nav || nav.querySelector('[data-help-link]')) return;
  const link = helpElement("a");
  link.href = "/help.html";
  link.dataset.helpLink = "true";
  const icon = helpElement("span", "?");
  icon.setAttribute("aria-hidden", "true");
  link.append(icon, helpElement("span", " Справка"));
  nav.append(link);
}
