// frontend/src/js/features/review/manual-editor.js

/**
 * Диалог текста и нормативного основания ручного Gold-замечания.
 * Проверка и сохранение передаются контроллеру; диалог не знает API и геометрию.
 */

function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  node.className = className;

  if (text !== null) {
    node.textContent = text;
  }

  return node;
}

/** Создаёт доступную форму ввода с отменой по кнопке и Escape. */
export function createManualEditor(pageNumber, onSave, onCancel) {
  const dialog = element("dialog", "manual-editor");

  dialog.setAttribute("aria-labelledby", `manualTitle${pageNumber}`);

  const form = element("form", "manual-editor__form");

  const title = element(
    "h3",
    "manual-editor__title",
    "Gold · замечание пользователя",
  );

  title.id = `manualTitle${pageNumber}`;

  const textLabel = element(
    "label",
    "manual-editor__label",
    "Текст замечания",
  );

  textLabel.htmlFor = `manualText${pageNumber}`;

  const text = element("textarea", "manual-editor__textarea");

  text.id = textLabel.htmlFor;
  text.required = true;
  text.maxLength = 10000;
  text.rows = 6;

  const normLabel = element(
    "label",
    "manual-editor__label",
    "Нормативное основание (при наличии)",
  );

  normLabel.htmlFor = `manualNorm${pageNumber}`;

  const norm = element("input", "manual-editor__input");

  norm.id = normLabel.htmlFor;
  norm.type = "text";
  norm.maxLength = 2000;

  const hint = element(
    "p",
    "manual-editor__hint",
    "Основание пока хранится как текст и не является проверенной ссылкой на норматив.",
  );

  const error = element("p", "manual-editor__error");

  error.setAttribute("role", "alert");

  const actions = element("div", "manual-editor__actions");

  const cancel = element(
    "button",
    "manual-editor__button",
    "Отмена",
  );

  cancel.type = "button";
  cancel.addEventListener("click", onCancel);

  const save = element(
    "button",
    "manual-editor__button manual-editor__button--save",
    "Сохранить локально",
  );

  save.type = "submit";

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    onSave(text.value, norm.value, error);
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    onCancel();
  });

  actions.append(cancel, save);

  form.append(
    title,
    textLabel,
    text,
    normLabel,
    norm,
    hint,
    error,
    actions,
  );

  dialog.append(form);

  return {
    dialog,

    open(value = "", normative = "") {
      text.value = value;
      norm.value = normative;
      error.textContent = "";

      dialog.showModal();
      text.focus();
    },

    close() {
      dialog.close();
    },
  };
}

