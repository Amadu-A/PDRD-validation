// frontend/src/js/features/experience/rows.js

/** Безопасные строки настоящего каталога и миниатюры всех подтверждённых областей. */
import { DECISION_LABELS, experienceTagLabel } from "./labels.js";

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
export function experienceRow(example, { api, onImage, onEdit, onHistory }) {
  const row = element("tr");
  row.dataset.experienceId = example.id;
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
  const documentCell = element("td");
  documentCell.append(element("strong", "experience-table__title", example.document_title),
    element("span", "experience-table__detail", `${example.source_filename} · лист ${example.page_number}`),
    element("span", "experience-table__detail", `Создано ${new Date(example.created_at).toLocaleString("ru")}`),
    element("span", "experience-table__detail", `Автор: ${example.source.created_by}`));
  const text = element("td");
  text.append(element("span", "experience-table__text", example.text),
    element("span", "experience-table__detail", example.normative_basis),
    element("span", "experience-table__detail", example.normative_reference));
  const tag = element("td");
  tag.append(element("strong", "experience-table__tag", experienceTagLabel(example.tag, example.decision)),
    element("span", "experience-table__detail", DECISION_LABELS[example.decision]),
    element("span", "experience-table__detail", LEARNING_LABELS[example.learning_use]));
  const state = element("td", "", example.source_current ? (example.active ? "Активно" : "Неактивно") : "Источник изменён или область отозвана");
  const actions = element("td");
  for (const [label, callback] of [["Редактировать", onEdit], ["История", onHistory]]) {
    const button = element("button", "experience-table__edit", label);
    button.type = "button";
    button.addEventListener("click", () => callback(example));
    actions.append(button);
  }
  row.append(preview, documentCell, text, tag, state, actions);
  return row;
}
