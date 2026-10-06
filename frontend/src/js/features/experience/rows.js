// frontend/src/js/features/experience/rows.js

/** Безопасные строки каталога: миниатюры исходных VLM и принятых инженером областей. */
import { REJECTION_REASONS } from "../review/rejection-reasons.js";
import { DECISION_LABELS, experienceTagLabel } from "./labels.js";
import { createTrashIcon } from "../../components/icons.js";

export const ROLE_LABELS = { platform_admin: "Администратор", department_head: "Руководитель отдела", designer: "Проектировщик" };

/** Актуальные имя и роль берутся из User Service; исторический actor отображается явно. */
export function authorLabel(author) {
  if (!author) return "Неизвестный автор";
  if (!author.resolved) return `${author.login || author.id} · профиль недоступен`;
  const roles = author.roles.map((role) => ROLE_LABELS[role] || role).join(", ");
  return [author.login, author.display_name, roles || "Без назначенной роли"].filter(Boolean).join(" · ");
}

export const LEARNING_LABELS = {
  positive: "Положительный пример", negative: "Отрицательный пример",
  needs_adjudication: "Требует уточнения перед обучением",
};

export function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

/** Идентификаторы и пользовательский текст никогда не интерполируются в HTML. */
export function experienceRow(example, { api, onImage, onEdit, onHistory, onDelete, onSelect, selected = false, memberRevision = null, canCurate = true, canSelect = true }) {
  const row = element("tr");
  row.className = memberRevision !== null ? "experience-table__row--member" : "";
  row.dataset.experienceId = example.id;
  const selection = element("td", "experience-table__selection");
  const checkbox = element("input");
  checkbox.type = "checkbox"; checkbox.checked = selected;
  checkbox.disabled = !canSelect; selection.hidden = !canSelect;
  checkbox.setAttribute("aria-label", `Выбрать замечание: ${example.text.slice(0, 80)}`);
  checkbox.addEventListener("change", () => onSelect?.(example, checkbox.checked));
  selection.append(checkbox);
  const preview = element("td");
  example.crops.forEach((crop, index) => {
    const button = element("button", "experience-table__preview");
    button.type = "button";
    button.setAttribute("aria-label", `Открыть область ${index + 1}: ${example.text.slice(0, 80)}`);
    const image = element("img");
    image.src = api.imageUrl(example.id, index);
    image.alt = `Область ${index + 1} замечания`;
    image.loading = "lazy";
    image.decoding = "async";
    image.width = crop.width;
    image.height = crop.height;
    button.append(image);
    button.addEventListener("click", () => onImage(example, index));
    preview.append(button);
  });
  if (!example.crops.length) preview.append(element("span", "experience-table__detail", "Нет пригодной области"));
  const documentCell = element("td");
  documentCell.append(element("strong", "experience-table__title", example.document_title),
    element("span", "experience-table__detail", `${example.source_filename} · лист ${example.page_number}`));
  const text = element("td");
  text.append(element("span", "experience-table__text", example.text),
    element("span", "experience-table__detail", example.normative_basis),
    element("span", "experience-table__detail", example.normative_reference));
  if (example.source.reason_category) {
    text.append(element("span", "experience-table__detail", `Причина: ${REJECTION_REASONS[example.source.reason_category] ?? example.source.reason_category}`));
    if (example.source.comment) text.append(element("span", "experience-table__detail", `Комментарий: ${example.source.comment}`));
  }
  const tag = element("td");
  tag.append(element("strong", "experience-table__tag", experienceTagLabel(example.tag, example.decision)),
    element("span", "experience-table__detail", DECISION_LABELS[example.decision]),
    element("span", "experience-table__detail", LEARNING_LABELS[example.learning_use]));
  const state = element("td", "", example.source_current ? (example.active ? "Активно" : "Неактивно") : "Источник изменён или область отозвана");
  if (example.training_eligible === false) state.append(element("span", "experience-table__detail", "Не допускается к обучению"));
  if (memberRevision !== null && memberRevision !== example.revision) state.append(element("span", "experience-table__detail", `В версии сохранена редакция ${memberRevision}`));
  const section = element("td", "", example.section_title || "Не указан");
  section.append(element("span", "experience-table__detail", example.section_id || ""));
  const author = element("td", "", authorLabel(example.author ?? {
    id: example.source.created_by, login: example.source.created_by, resolved: false,
  }));
  const created = element("td", "experience-table__date", new Date(example.created_at).toLocaleString("ru"));
  const actions = element("td", "experience-table__actions");
  for (const [icon, label, callback] of [["✎", "Редактировать", onEdit], ["◷", "История", onHistory], [null, "Удалить замечание", onDelete]]) {
    if (!canCurate && label !== "История") continue;
    const button = element("button", "experience-table__icon", icon);
    button.type = "button";
    button.title = label; button.setAttribute("aria-label", label);
    if (icon === null) { button.textContent = ""; button.append(createTrashIcon()); }
    button.addEventListener("click", () => callback?.(example));
    actions.append(button);
  }
  row.append(selection, preview, documentCell, text, tag, state, section, author, created, actions);
  return row;
}
