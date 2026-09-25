// frontend/src/js/features/review/controller.js

/**
 * UI-адаптер локального review для существующих visual findings.
 * Изменения видимы до перезагрузки отчёта, серверное сохранение появится позже.
 */

import { createReviewControls } from "./controls.js";
import { createReviewState, REVIEW_DECISIONS } from "./state.js";

const VISUALIZATION_SELECTOR = ".analysis-result__visualization";
const TEXT_SELECTOR = (
  ".analysis-result__annotation-title, .analysis-result__group-member-text"
);


function createEditor(onSave) {
  const dialog = document.createElement("dialog");
  dialog.className = "review-editor";
  dialog.setAttribute("aria-labelledby", "reviewEditorTitle");

  const title = document.createElement("h3");
  title.id = "reviewEditorTitle";
  title.className = "review-editor__title";
  title.textContent = "Редактирование замечания";

  const label = document.createElement("label");
  label.className = "review-editor__label";
  label.htmlFor = "reviewEditorText";
  label.textContent = "Текст замечания на карточке";

  const textarea = document.createElement("textarea");
  textarea.id = "reviewEditorText";
  textarea.className = "review-editor__textarea";
  textarea.maxLength = 10000;
  textarea.rows = 8;

  const error = document.createElement("p");
  error.className = "review-editor__error";
  error.setAttribute("role", "alert");

  const actions = document.createElement("div");
  actions.className = "review-editor__actions";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "review-editor__button";
  cancel.textContent = "Отмена";
  cancel.addEventListener("click", () => dialog.close());

  const save = document.createElement("button");
  save.type = "button";
  save.className = "review-editor__button review-editor__button--save";
  save.dataset.reviewSave = "";
  save.textContent = "Сохранить правку локально";
  save.addEventListener("click", () => onSave(textarea.value, error, dialog));

  actions.append(cancel, save);
  dialog.append(title, label, textarea, error, actions);

  return {
    dialog,
    open(text) {
      textarea.value = text;
      error.textContent = "";
      dialog.showModal();
      textarea.focus();
    },
  };
}


/** Монтирует один review-controller после отображения нового отчёта. */
export function createReviewController() {
  let state = createReviewState();
  let views = new Map();
  let activeFindingId = null;
  let preview = null;
  let editor = null;

  function updatePreview() {
    if (!preview) {
      return;
    }
    const counts = state.summary();
    preview.textContent = (
      `Предпросмотр решений: ${counts.pending} без решения, `
      + `${counts.accepted} принято, ${counts.rejected} отклонено. `
      + "Изменения пока не сохраняются на сервере и не влияют на PDF."
    );
  }

  function refresh(findingId) {
    const entry = state.get(findingId);
    const statusLabels = {
      pending: entry.edited ? "Gold · ждёт решения" : "Ожидает решения",
      accepted: "Wise · принято локально",
      rejected: "Bad · отклонено локально",
    };

    for (const view of views.get(findingId) ?? []) {
      view.text.textContent = entry.text;
      view.item.dataset.reviewDecision = entry.decision;
      view.item.dataset.reviewEdited = String(entry.edited);
      view.controls.status.textContent = statusLabels[entry.decision];
      view.controls.accept.setAttribute(
        "aria-pressed", String(entry.decision === REVIEW_DECISIONS.ACCEPTED),
      );
      view.controls.reject.setAttribute(
        "aria-pressed", String(entry.decision === REVIEW_DECISIONS.REJECTED),
      );
      view.controls.edit.setAttribute("aria-pressed", String(entry.edited));
    }
    updatePreview();
  }

  function decide(findingId, decision) {
    state.decide(findingId, decision);
    refresh(findingId);
  }

  function mount(root) {
    state = createReviewState();
    views = new Map();
    activeFindingId = null;
    preview = null;
    editor = null;

    const visualization = root.querySelector(VISUALIZATION_SELECTOR);
    const items = visualization?.querySelectorAll("[data-finding-id]") ?? [];
    if (!items.length) {
      return;
    }

    editor = createEditor((value, error, dialog) => {
      if (!activeFindingId) {
        return;
      }
      try {
        state.edit(activeFindingId, value);
        refresh(activeFindingId);
        dialog.close();
      } catch (problem) {
        error.textContent = problem.message;
      }
    });
    root.append(editor.dialog);

    for (const item of items) {
      const findingId = String(item.dataset.findingId ?? "").trim();
      const text = item.querySelector(TEXT_SELECTOR);
      if (!findingId || !text || !text.textContent.trim()) {
        continue;
      }
      state.register(findingId, text.textContent);
      item.dataset.reviewItem = "";

      const controls = createReviewControls({
        onAccept: () => decide(findingId, REVIEW_DECISIONS.ACCEPTED),
        onReject: () => decide(findingId, REVIEW_DECISIONS.REJECTED),
        onEdit: () => {
          activeFindingId = findingId;
          editor.open(state.get(findingId).text);
        },
      });
      item.prepend(controls.element);
      const found = views.get(findingId) ?? [];
      found.push({ item, text, controls });
      views.set(findingId, found);
    }

    if (views.size === 0) {
      editor.dialog.remove();
      return;
    }
    preview = document.createElement("p");
    preview.className = "review-preview";
    preview.setAttribute("role", "status");
    preview.dataset.reviewPreview = "";
    visualization.prepend(preview);
    for (const findingId of views.keys()) {
      refresh(findingId);
    }
  }

  return { mount };
}