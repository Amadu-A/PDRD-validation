// frontend/src/js/features/experience/page.js

/** Отображает демонстрационную таблицу Experience без ложного сохранения. */
import { createExperienceModel } from "./model.js";

const model = createExperienceModel();
const filter = document.querySelector("[data-experience-filter]");
const rows = document.querySelector("[data-experience-rows]");
const count = document.querySelector("[data-experience-count]");
const imageDialog = document.querySelector("[data-experience-image-dialog]");
const image = document.querySelector("[data-experience-large-image]");
const editDialog = document.querySelector("[data-experience-edit-dialog]");
const editForm = document.querySelector("[data-experience-edit-form]");
const editError = document.querySelector("[data-experience-edit-error]");
let editingId = null;

/** Создаёт элемент только с безопасным текстовым содержимым. */
function element(tag, className, value = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value;
  return node;
}

/** Возвращает схему для явно вымышленной миниатюры. */
function demoImage() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" viewBox="0 0 480 300"><rect width="480" height="300" fill="#f4f5f7"/><path d="M30 60h420M30 125h420M30 190h420M120 25v250M250 25v250M380 25v250" stroke="#a5b4c8" stroke-width="4"/><rect x="150" y="85" width="165" height="90" fill="none" stroke="#b45309" stroke-width="7"/><text x="240" y="260" text-anchor="middle" font-family="sans-serif" font-size="18" fill="#475569">Демонстрационная область</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** Читает значения фильтров без изменения модели. */
function criteria() {
  return Object.fromEntries(new FormData(filter).entries());
}

/** Создаёт строку таблицы с отдельными кнопками просмотра и правки. */
function row(example) {
  const tr = document.createElement("tr");
  tr.dataset.experienceId = example.id;

  const previewCell = document.createElement("td");
  const previewButton = element("button", "experience-table__preview");
  previewButton.type = "button";
  previewButton.setAttribute("aria-label", `Открыть ${example.crop_label}`);
  const thumbnail = document.createElement("img");
  thumbnail.src = demoImage();
  thumbnail.alt = example.crop_label;
  previewButton.append(thumbnail);
  previewButton.addEventListener("click", () => {
    image.src = thumbnail.src;
    image.alt = example.crop_label;
    imageDialog.showModal();
  });
  previewCell.append(previewButton);

  const documentCell = document.createElement("td");
  documentCell.append(
    element("strong", "experience-table__title", example.document_title),
    element("span", "experience-table__detail", example.source_filename),
    element("span", "experience-table__detail", `Лист ${example.page_number}`),
  );

  const textCell = document.createElement("td");
  textCell.append(
    element("span", "experience-table__text", example.text),
    element("span", "experience-table__detail", example.normative_basis),
  );

  const tagCell = document.createElement("td");
  const learningLabels = {
    positive: "Положительный пример",
    negative: "Отрицательный пример",
    needs_adjudication: "Требует уточнения перед обучением",
  };
  tagCell.append(
    element("strong", "experience-table__tag", example.tag),
    element("span", "experience-table__detail", example.decision),
    element("span", "experience-table__detail",
      learningLabels[example.learning_use] ?? "Не определено"),
  );

  const activeCell = document.createElement("td");
  activeCell.textContent = example.active ? "Активно" : "Неактивно";

  const actionCell = document.createElement("td");
  const editButton = element("button", "experience-table__edit", "Редактировать");
  editButton.type = "button";
  editButton.addEventListener("click", () => {
    editingId = example.id;
    editForm.elements.namedItem("text").value = example.text;
    editForm.elements.namedItem("normative_basis").value = example.normative_basis;
    editForm.elements.namedItem("active").checked = example.active;
    editError.textContent = "";
    editDialog.showModal();
    editForm.elements.namedItem("text").focus();
  });
  actionCell.append(editButton);

  tr.append(previewCell, documentCell, textCell, tagCell, activeCell, actionCell);
  return tr;
}

/** Перерисовывает только таблицу после фильтрации или локальной правки. */
function render() {
  const visible = model.list(criteria());
  rows.replaceChildren(...visible.map(row));
  count.textContent = `Показано ${visible.length} из ${model.list().length} примеров.`;
}

filter.addEventListener("input", render);
filter.addEventListener("change", render);
document.querySelector("[data-experience-image-close]").addEventListener("click", () => {
  imageDialog.close();
});
document.querySelector("[data-experience-edit-close]").addEventListener("click", () => {
  editDialog.close();
});
editForm.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    model.update(editingId, {
      text: editForm.elements.namedItem("text").value,
      normative_basis: editForm.elements.namedItem("normative_basis").value,
      active: editForm.elements.namedItem("active").checked,
    });
    editDialog.close();
    render();
  } catch (error) {
    editError.textContent = error.message;
  }
});

render();
