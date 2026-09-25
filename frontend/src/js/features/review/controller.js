// frontend/src/js/features/review/controller.js

/**
 * UI-адаптер решений для VLM и ручных Gold-замечаний.
 * Решение и происхождение сохраняются независимо в локальной модели.
 */

import { createReviewControls } from "./controls.js";
import { createManualAnnotationController } from "./manual.js";
import { createReviewState, REVIEW_DECISIONS, REVIEW_ORIGINS } from "./state.js";

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
  title.textContent = "Редактирование замечания VLM";

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

  save.addEventListener(
    "click",
    () => onSave(textarea.value, error, dialog),
  );

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

/** Связывает существующую визуализацию с ручным review. */
export function createReviewController() {
  let state = createReviewState();
  let views = new Map();
  let preview = null;
  let editor = null;
  let activeFindingId = null;

  const manual = createManualAnnotationController({
    onCreate(note) {
      state.register(note.findingId, note.text, {
        origin: REVIEW_ORIGINS.MANUAL,
        normativeSection: note.normativeSection,
      });

      attach(
        note.card,
        note.findingId,
        note.textNode,
        note.onEdit,
      );
    },

    onUpdate(findingId, text, normativeSection) {
      const entry = state.edit(
        findingId,
        text,
        { normativeSection },
      );

      refresh(findingId);

      return entry;
    },

    onGet(findingId) {
      return state.get(findingId);
    },
  });

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

    const tagLabels = {
      wise: "Wise",
      bad: "Bad",
      edited: "Edited",
      gold: "Gold",
    };

    const decisionLabels = {
      pending: "ожидает решения",
      accepted: "принято локально",
      rejected: "отклонено локально",
    };

    const tag = tagLabels[entry.experienceTag] ?? "VLM";

    for (const view of views.get(findingId) ?? []) {
      view.text.textContent = entry.text;

      view.item.dataset.reviewDecision = entry.decision;
      view.item.dataset.reviewOrigin = entry.origin;
      view.item.dataset.reviewTag = entry.experienceTag ?? "";
      view.item.dataset.reviewEdited = String(entry.edited);

      view.controls.status.textContent = (
        `${tag} · ${decisionLabels[entry.decision]}`
      );

      view.controls.accept.setAttribute(
        "aria-pressed",
        String(entry.decision === REVIEW_DECISIONS.ACCEPTED),
      );

      view.controls.reject.setAttribute(
        "aria-pressed",
        String(entry.decision === REVIEW_DECISIONS.REJECTED),
      );

      view.controls.edit.setAttribute(
        "aria-pressed",
        String(entry.edited),
      );
    }

    updatePreview();
  }

  function attach(item, findingId, text, customEdit = null) {
    item.dataset.reviewItem = "";

    const controls = createReviewControls({
      onAccept() {
        state.decide(
          findingId,
          REVIEW_DECISIONS.ACCEPTED,
        );

        refresh(findingId);
      },

      onReject() {
        state.decide(
          findingId,
          REVIEW_DECISIONS.REJECTED,
        );

        refresh(findingId);
      },

      onEdit() {
        if (customEdit) {
          customEdit();
        } else {
          activeFindingId = findingId;

          editor.open(
            state.get(findingId).text,
          );
        }
      },
    });

    item.prepend(controls.element);

    const found = views.get(findingId) ?? [];

    found.push({
      item,
      text,
      controls,
    });

    views.set(findingId, found);

    refresh(findingId);
  }

  function mount(root) {
    state = createReviewState();
    views = new Map();
    activeFindingId = null;
    preview = null;
    editor = null;

    const visualization = root.querySelector(
      ".analysis-result__visualization",
    );

    if (!visualization) {
      return;
    }

    const items = visualization.querySelectorAll(
      "[data-finding-id]",
    );

    const pages = visualization.querySelectorAll(
      ".analysis-result__page-visualization",
    );

    if (!items.length && !pages.length) {
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
      const findingId = String(
        item.dataset.findingId ?? "",
      ).trim();

      const text = item.querySelector(TEXT_SELECTOR);

      if (
        !findingId
        || !text
        || !text.textContent.trim()
      ) {
        continue;
      }

      state.register(
        findingId,
        text.textContent,
      );

      attach(item, findingId, text);
    }

    const manualPages = manual.mount(
      visualization,
      root,
    );

    if (!views.size && !manualPages) {
      editor.dialog.remove();
      return;
    }

    preview = document.createElement("p");

    preview.className = "review-preview";
    preview.setAttribute("role", "status");
    preview.dataset.reviewPreview = "";

    visualization.prepend(preview);

    updatePreview();
  }

  return { mount };
}