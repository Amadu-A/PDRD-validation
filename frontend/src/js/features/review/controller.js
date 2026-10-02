// frontend/src/js/features/review/controller.js

/**
 * UI-адаптер решений для VLM и ручных Gold-замечаний.
 * Карточка на листе, текстовый список и данные экспорта используют одно решение.
 * Сетевой адаптер подключается снаружи через onChange/hydrate.
 */

import { createReviewControls } from "./controls.js";
import { createManualAnnotationController } from "./manual.js";
import { createManualTextList } from "./manual-list.js";
import { createManualRecords } from "./manual-records.js";
import { createAutomaticReview } from "./automatic.js";
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
  save.textContent = "Сохранить правку";

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

/** Связывает существующую визуализацию с ручным review и текстовым отчётом. */
export function createReviewController({
  onChange = () => {},
  capabilities = { canCreateGold: true, canDecide: true },
} = {}) {
  const canCreateGold = capabilities.canCreateGold !== false;
  const canDecide = capabilities.canDecide !== false;
  let locked = false;
  let persistent = false;
  let syncMessage = "";
  let hydrating = false;
  let state = createReviewState();
  let views = new Map();
  let preview = null;
  let editor = null;
  let activeFindingId = null;
  let reportRoot = null;
  let manualRecords = createManualRecords();
  let emptyMessages = new Map();
  let manualNotes = new Map();

  const manualList = createManualTextList();

  function markManualOnPage(pageNumber) {
    const pages = reportRoot?.querySelectorAll?.(
      ".analysis-result__page-visualization",
    ) ?? [];

    for (const page of pages) {
      const title = page.querySelector(".analysis-result__page-title");
      if (title?.textContent?.trim() !== `Лист/страница ${pageNumber}`) {
        continue;
      }
      const empty = page.querySelector(".analysis-result__page-empty");
      if (empty) {
        if (!emptyMessages.has(pageNumber)) {
          emptyMessages.set(pageNumber, empty.textContent);
        }
        empty.textContent = manualRecords.snapshot().some(
          (note) => (
            note.page_number === pageNumber
            && note.decision !== REVIEW_DECISIONS.REJECTED
          ),
        ) ? (
          "Автоматические замечания на этом листе отсутствуют. "
          + "Замечание добавлено пользователем."
        ) : emptyMessages.get(pageNumber);
      }
      break;
    }
  }

  const manual = createManualAnnotationController({
    isEnabled: () => !locked && canCreateGold,
    onInteractionStart: () => automatic.cancelActive(),
    onCreate(note) {
      const entry = state.register(note.findingId, note.text, {
        origin: REVIEW_ORIGINS.MANUAL,
        normativeSection: note.normativeSection,
      });

      attachManual(note, entry);
    },

    onUpdate(findingId, text, normativeSection) {
      if (!canCreateGold) throw new Error("Недостаточно прав для Gold-замечания.");
      if (locked) throw new Error("Дождитесь восстановления Review.");
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

    onGeometry(findingId, issueBox, calloutBox) {
      const changed = manualRecords.updateGeometry(findingId, issueBox, calloutBox);
      if (changed) {
        state.invalidateManual(findingId);
        refresh(findingId);
      }
      return changed;
    },

    onRemove(note) {
      if (persistent) {
        // Undo добавления остаётся аудируемым отклонением сохранённой Gold-записи.
        const entry = state.decide(note.findingId, REVIEW_DECISIONS.REJECTED);
        refresh(note.findingId);
        return entry;
      }
      const entry = state.removeManual(note.findingId);
      manualRecords.remove(note.findingId);
      manualList.remove(note.findingId);
      for (const view of views.get(note.findingId) ?? []) {
        view.controls.element.remove();
      }
      views.delete(note.findingId);
      manualNotes.delete(note.findingId);
      markManualOnPage(note.pageNumber);
      updatePreview();
      return entry;
    },
  });

  const automatic = createAutomaticReview({
    isEnabled: () => !locked && canDecide,
    onGeometry(findingIds) {
      for (const findingId of findingIds) {
        state.invalidate(findingId);
        refresh(findingId);
      }
    },
    onRecord: (pageNumber, label, undo) => manual.recordAction(pageNumber, label, undo),
    onStart: () => manual.cancelActive(),
  });

  /** Подключает одну Gold-запись к обоим представлениям без дублирования данных. */
  function attachManual(note, entry) {
    manualNotes.set(note.findingId, note);
    manualRecords.add(note, entry);
    const textView = manualList.add(note, entry);
    markManualOnPage(note.pageNumber);
    attach(note.card, note.findingId, note.textNode, note.onEdit, note);
    if (textView) {
      attach(textView.article, note.findingId, textView.textNode, note.onEdit, note);
    }
  }

  function updatePreview() {
    if (!preview) {
      return;
    }

    const counts = state.summary();

    preview.textContent = (
      `Предпросмотр решений: ${counts.pending} без решения, `
      + `${counts.accepted} принято, ${counts.rejected} отклонено. `
      + (syncMessage || "Изменения пока не сохраняются на сервере и не влияют на PDF.")
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
      accepted: persistent ? "принято" : "принято локально",
      rejected: persistent ? "отклонено" : "отклонено локально",
    };

    const tag = tagLabels[entry.experienceTag] ?? "VLM";

    for (const view of views.get(findingId) ?? []) {
      view.text.textContent = entry.text;
      // Полный текст остаётся актуальным после правки и серверного восстановления.
      const tooltipText = view.tooltipText;
      if (tooltipText) tooltipText.textContent = entry.text;
      if (view.basisText) view.basisText.textContent = entry.normativeSection || "Не указано.";

      view.item.dataset.reviewDecision = entry.decision;
      view.item.dataset.reviewOrigin = entry.origin;
      view.item.dataset.reviewTag = entry.experienceTag ?? "";
      view.item.dataset.reviewEdited = String(entry.edited);
      view.item.classList.toggle("is-hidden", entry.decision === REVIEW_DECISIONS.REJECTED);

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
      for (const key of ["accept", "reject"]) {
        view.controls[key].disabled = locked || !canDecide;
      }
      view.controls.edit.disabled = locked || !(entry.origin === REVIEW_ORIGINS.MANUAL
        ? canCreateGold : canDecide);
    }

    manualList.sync(entry);
    manualRecords.sync(entry);
    manualNotes.get(findingId)?.applyText(entry.text, entry.normativeSection);
    manualNotes.get(findingId)?.setRejected(entry.decision === REVIEW_DECISIONS.REJECTED);
    automatic.setRejected(findingId, entry.decision === REVIEW_DECISIONS.REJECTED);
    const note = manualNotes.get(findingId);
    if (note) markManualOnPage(note.pageNumber);
    updatePreview();
    if (!hydrating) onChange();
  }

  /** Решение и его Undo сохраняют текст, источник, области и независимый тег. */
  function decide(findingId, decision, pageNumber) {
    if (locked || !canDecide) return;
    const previous = state.get(findingId).decision;
    if (previous === decision) return;
    manual.cancelActive();
    automatic.cancelActive();
    state.decide(findingId, decision);
    refresh(findingId);
    manual.recordAction(pageNumber, "решение по замечанию", () => {
      state.restoreDecision(findingId, previous);
      refresh(findingId);
    });
  }

  function attach(item, findingId, text, customEdit = null, note = null) {
    item.dataset.reviewItem = "";
    const pageNumber = note?.pageNumber ?? Number(item.dataset.reviewPage);

    const controls = createReviewControls({
      onAccept() {
        decide(findingId, REVIEW_DECISIONS.ACCEPTED, pageNumber);
      },

      onReject() {
        decide(findingId, REVIEW_DECISIONS.REJECTED, pageNumber);
      },

      onEdit() {
        if (locked) return;
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

    controls.accept.hidden = !canDecide;
    controls.reject.hidden = !canDecide;
    controls.edit.hidden = note ? !canCreateGold : !canDecide;

    item.prepend(controls.element);

    const found = views.get(findingId) ?? [];

    found.push({
      item,
      text,
      controls,
      tooltipText: item.querySelector("[data-review-tooltip-text]"),
      basisText: item.querySelector("[data-review-tooltip-basis]") ?? item.querySelector("[data-review-basis-text]"),
    });

    views.set(findingId, found);

    refresh(findingId);
  }

  function mount(root) {
    automatic.dispose();
    manual.dispose();
    state = createReviewState();
    views = new Map();
    manualRecords = createManualRecords();
    emptyMessages = new Map();
    manualNotes = new Map();
    activeFindingId = null;
    preview = null;
    editor = null;
    reportRoot = root;

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

    manualList.mount(root);

    editor = createEditor((value, error, dialog) => {
      if (!activeFindingId) {
        return;
      }

      try {
        if (locked) throw new Error("Дождитесь восстановления Review.");
        state.edit(activeFindingId, value);
        refresh(activeFindingId);
        dialog.close();

      } catch (problem) {
        error.textContent = problem.message;
      }
    });

    root.append(editor.dialog);

    const textItems = root.querySelectorAll(".analysis-result__finding[data-finding-id]");
    for (const item of [...textItems, ...items]) {
      const findingId = String(
        item.dataset.findingId ?? "",
      ).trim();

      const text = item.querySelector("[data-review-text]") ?? item.querySelector(TEXT_SELECTOR);

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
        { normativeSection: item.dataset.reviewBasis ?? "" },
      );

      attach(item, findingId, text);
    }

    const manualPages = manual.mount(
      visualization,
      root,
    );
    automatic.mount(visualization);

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

  /** Снимок Gold для отдельного модуля серверного сохранения. */
  function getManualSnapshot() {
    return manualRecords.snapshot();
  }

  /** Операционный снимок включает rejected; серверное сохранение подключается отдельно. */
  function getReviewSnapshot() {
    return state.snapshot().map((entry) => ({
      ...entry,
      visualizations: automatic.snapshot(entry.findingId),
    }));
  }

  /** Восстанавливает данные и обе геометрии до разблокировки контролов. */
  function hydrate(session) {
    hydrating = true;
    try {
      state.hydrate(session.findings);
      automatic.restore(session.findings.filter((row) => row.origin === "vlm"));
      manual.restore(session.findings.filter((row) => row.origin === "manual"));
      for (const entry of state.snapshot()) refresh(entry.findingId);
    } finally {
      hydrating = false;
    }
  }

  /** Отделяет состояние подключения от принятия конкретных замечаний. */
  function setConnection({ isPersistent = persistent, message = syncMessage, isLocked = locked }) {
    persistent = isPersistent;
    locked = isLocked;
    syncMessage = message;
    if (locked) { manual.cancelActive(); automatic.cancelActive(); }
    for (const rows of views.values()) for (const view of rows) {
      for (const key of ["accept", "reject"]) view.controls[key].disabled = locked || !canDecide;
      view.controls.edit.disabled = locked || !(view.item.dataset.reviewOrigin === REVIEW_ORIGINS.MANUAL
        ? canCreateGold : canDecide);
    }
    for (const button of reportRoot?.querySelectorAll("[data-review-page-add], [data-review-page-undo]") ?? []) {
      if (button.dataset.reviewPageUndo !== undefined) continue;
      button.disabled = locked || !canCreateGold;
    }
    updatePreview();
  }

  return {
    mount, getManualSnapshot, getReviewSnapshot, hydrate, setConnection,
    dispose() { manual.dispose(); automatic.dispose(); editor?.dialog.remove(); },
  };
}
