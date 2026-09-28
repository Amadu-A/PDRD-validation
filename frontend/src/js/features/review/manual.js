// frontend/src/js/features/review/manual.js

/**
 * Создание, изменение областей и отмена локальных Gold-находок на одном листе.
 * Две области выбираются на изображении одного листа; координаты 0..1000.
 * Все операции локальные, серверное сохранение здесь отсутствует.
 */

import {
  boxBetween,
  normalizedPoint,
  positionBox,
  validBox,
} from "./geometry.js";

import { createManualEditor } from "./manual-editor.js";
import { createManualLine, createManualNote } from "./manual-note.js";
import { createManualHistory } from "./history.js";

const PAGE_SELECTOR = ".analysis-result__page-visualization";

function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  node.className = className;

  if (text !== null) {
    node.textContent = text;
  }

  return node;
}

function pageNumberOf(page) {
  const label = page.querySelector(".analysis-result__page-title")?.textContent ?? "";
  const match = /(?:Лист\/страница)\s+(\d+)\s*$/.exec(label);

  return match ? Number(match[1]) : null;
}

/**
 * Интегрирует ручные области с уже существующим review controller.
 * Каждый контроллер страницы хранит свою геометрию, поэтому области не могут
 * случайно пересечь границу соседнего листа.
 */
export function createManualAnnotationController({
  onCreate,
  onUpdate,
  onGet,
  onGeometry,
  onRemove,
  onRestore,

  idFactory = () => (
    globalThis.crypto?.randomUUID?.()
    ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`
  ),
}) {
  let activeAbort = null;
  let disposeHandlers = [];

  /** Подписывает обработчик только на время жизни текущего отчёта. */
  function listen(target, event, handler) {
    target.addEventListener(event, handler);
    disposeHandlers.push(() => target.removeEventListener(event, handler));
  }

  /** Закрывает черновик и освобождает подписки прежнего отчёта. */
  function dispose() {
    activeAbort?.();
    activeAbort = null;
    for (const cleanup of disposeHandlers) cleanup();
    disposeHandlers = [];
  }

  function mountPage(page, root) {
    const pageNumber = pageNumberOf(page);

    const pane = page.querySelector(
      ".analysis-result__page-image-pane",
    );

    const image = page.querySelector(
      ".analysis-result__page-image",
    );

    const stage = page.querySelector(
      ".analysis-result__page-stage",
    );

    if (pageNumber === null || !pane || !image || !stage) {
      return;
    }

    const toolbar = element(
      "div",
      "manual-annotation__toolbar",
    );

    const add = element(
      "button",
      "manual-annotation__button",
      "+ Добавить замечание",
    );

    add.type = "button";
    add.dataset.reviewPageAdd = String(pageNumber);

    const cancel = element(
      "button",
      "manual-annotation__button",
      "Отменить выделение",
    );

    cancel.type = "button";
    cancel.hidden = true;

    const message = element(
      "span",
      "manual-annotation__message",
      "Добавление вручную: Gold",
    );

    message.setAttribute("role", "status");

    toolbar.append(add, cancel, message);
    const saveGeometry = element("button", "manual-annotation__button", "Сохранить области");
    saveGeometry.type = "button";
    saveGeometry.dataset.reviewGeometrySave = "";
    saveGeometry.hidden = true;
    const undo = element("button", "manual-annotation__button", "Отменить действие");
    undo.type = "button";
    undo.dataset.reviewPageUndo = String(pageNumber);
    undo.disabled = true;
    toolbar.append(saveGeometry, undo);
    page.insertBefore(toolbar, stage);

    const layer = element(
      "div",
      "manual-annotation__layer",
    );

    layer.dataset.manualMode = "idle";
    layer.tabIndex = -1;

    layer.setAttribute(
      "aria-label",
      `Выделение на странице ${pageNumber}`,
    );

    const draftIssue = element(
      "div",
      "manual-annotation__draft manual-annotation__draft--issue",
    );

    const draftCallout = element(
      "div",
      "manual-annotation__draft manual-annotation__draft--callout",
    );

    draftIssue.hidden = true;
    draftCallout.hidden = true;

    layer.append(draftIssue, draftCallout);
    pane.append(layer);

    let mode = "idle";
    let dragging = null;
    let issue = null;
    let callout = null;
    let editingNote = null;
    let geometryNote = null;
    let draftLine = null;
    const history = createManualHistory();

    /** Указывает конкретное действие, которое отменит следующая кнопка Undo. */
    function syncHistory() {
      undo.disabled = mode !== "idle" || !history.label;
      undo.textContent = history.label ? `Отменить ${history.label}` : "Отменить действие";
      undo.title = history.label ? `Отменить: ${history.label}` : "Нет действий для отмены";
    }

    function syncSize() {
      const bounds = image.getBoundingClientRect();
      const parent = pane.getBoundingClientRect();

      layer.style.left = `${bounds.left - parent.left}px`;
      layer.style.top = `${bounds.top - parent.top}px`;
      layer.style.width = `${bounds.width}px`;
      layer.style.height = `${bounds.height}px`;
    }

    function setMode(next, description) {
      mode = next;
      layer.dataset.manualMode = next;
      message.textContent = description;
      cancel.hidden = next === "idle";
      add.disabled = next !== "idle";
      saveGeometry.hidden = next !== "confirm";
      syncHistory();
    }

    function resetDraft() {
      issue = null;
      callout = null;
      if (dragging && layer.hasPointerCapture(dragging.pointerId)) {
        layer.releasePointerCapture(dragging.pointerId);
      }
      dragging = null;

      draftIssue.hidden = true;
      draftCallout.hidden = true;

      editingNote = null;
      geometryNote = null;
      draftLine?.remove();
      draftLine = null;

      setMode(
        "idle",
        "Добавление вручную: Gold",
      );

      if (activeAbort === abort) {
        activeAbort = null;
      }
    }

    function abort() {
      if (editingNote) {
        editor.close();
        editingNote = null;
        add.focus();
        return;
      }

      if (mode === "editing") {
        editor.close();
      }

      resetDraft();
      add.focus();
    }

    function createNote(text, normativeSection) {
      const findingId = `manual:${idFactory()}`;
      const note = createManualNote({
        findingId, pageNumber, text, normativeSection,
        issueBox: issue, calloutBox: callout,
      });
      note.onEdit = () => {
        activeAbort?.();
        activeAbort = abort;
        editingNote = note;
        const entry = onGet(note.findingId);
        editor.open(entry.text, entry.normativeSection);
      };
      note.onGeometry = () => {
        if (beginSelection()) geometryNote = note;
      };
      note.onRemove = () => {
        activeAbort?.();
        const snapshot = removeNote(note);
        history.record("удаление Gold", () => {
          note.applyText(snapshot.text, snapshot.normativeSection);
          onRestore(note, snapshot);
          layer.append(note.line, note.region, note.card);
        });
        syncHistory();
        undo.focus();
      };

      onCreate({ ...note, text, normativeSection });
      layer.append(note.line, note.region, note.card);
      history.record("добавление Gold", () => removeNote(note));
      syncHistory();
    }

    /** Сначала исключает канонические данные, затем убирает геометрию листа. */
    function removeNote(note) {
      const snapshot = onRemove(note);
      note.remove();
      return snapshot;
    }

    /** Применяет области к DOM только после изменения канонического снимка. */
    function applyGeometry(note, nextIssue, nextCallout) {
      const changed = onGeometry(note.findingId, nextIssue, nextCallout);
      if (changed) note.applyGeometry(nextIssue, nextCallout);
      return changed;
    }

    listen(saveGeometry, "click", () => {
      if (mode !== "confirm" || !geometryNote) return;
      const note = geometryNote;
      const previousIssue = { ...note.issueBox };
      const previousCallout = { ...note.calloutBox };
      if (applyGeometry(note, issue, callout)) {
        history.record("изменение областей Gold", () => {
          applyGeometry(note, previousIssue, previousCallout);
        });
      }
      resetDraft();
      message.textContent = "Области сохранены. Проверьте решение по замечанию.";
      add.focus();
    });

    listen(undo, "click", () => {
      activeAbort?.();
      if (history.undo()) message.textContent = "Локальное действие отменено.";
      syncHistory();
    });

    const editor = createManualEditor(
      pageNumber,

      (textValue, normValue, error) => {
        const text = String(textValue ?? "").trim();
        const normativeSection = String(normValue ?? "").trim();

        try {
          if (
            !text
            || text.length > 10000
            || normativeSection.length > 2000
          ) {
            throw new Error(
              "Проверьте текст (1–10000) и нормативное основание (до 2000 символов).",
            );
          }

          if (editingNote) {
            const entry = onUpdate(
              editingNote.findingId,
              text,
              normativeSection,
            );

            editingNote.normNode.textContent = entry.normativeSection
              ? `Нормативное основание: ${entry.normativeSection}`
              : "Нормативное основание не указано";

            editingNote = null;
            if (activeAbort === abort) activeAbort = null;

          } else {
            createNote(text, normativeSection);
            resetDraft();
          }

          editor.close();
          add.focus();

        } catch (problem) {
          error.textContent = (
            problem instanceof Error
              ? problem.message
              : String(problem)
          );
        }
      },

      abort,
    );

    root.append(editor.dialog);

    /** Начинает отдельный черновик на загруженном изображении этого листа. */
    function beginSelection() {
      const bounds = image.getBoundingClientRect();

      if (
        !(
          image.complete
          && image.naturalWidth > 0
          && bounds.width > 0
          && bounds.height > 0
        )
      ) {
        message.textContent = (
          "Дождитесь загрузки изображения листа."
        );

        return false;
      }

      activeAbort?.();

      activeAbort = abort;
      syncSize();

      setMode(
        "issue",
        "Обведите мышью область ошибки на этом листе. Esc — отмена.",
      );

      layer.focus();
      return true;
    }

    listen(add, "click", beginSelection);

    listen(cancel, "click", abort);

    listen(page, "keydown", (event) => {
      if (
        event.key === "Escape"
        && (mode === "issue" || mode === "callout" || mode === "confirm")
      ) {
        event.preventDefault();
        abort();
      }
    });

    listen(layer, "pointerdown", (event) => {
      if (
        (mode !== "issue" && mode !== "callout")
        || event.button !== 0
      ) {
        return;
      }

      event.preventDefault();

      const start = normalizedPoint(
        event.clientX,
        event.clientY,
        layer.getBoundingClientRect(),
      );

      dragging = {
        pointerId: event.pointerId,
        start,
        step: mode,
      };

      layer.setPointerCapture(event.pointerId);
    });

    listen(layer, "pointermove", (event) => {
      if (
        !dragging
        || dragging.pointerId !== event.pointerId
      ) {
        return;
      }

      const finish = normalizedPoint(
        event.clientX,
        event.clientY,
        layer.getBoundingClientRect(),
      );

      const box = boxBetween(
        dragging.start,
        finish,
      );

      const draft = dragging.step === "issue"
        ? draftIssue
        : draftCallout;

      positionBox(draft, box);
      draft.hidden = false;
    });

    listen(layer, "pointerup", (event) => {
      if (
        !dragging
        || dragging.pointerId !== event.pointerId
      ) {
        return;
      }

      const finish = normalizedPoint(
        event.clientX,
        event.clientY,
        layer.getBoundingClientRect(),
      );

      const box = boxBetween(
        dragging.start,
        finish,
      );

      const step = dragging.step;
      dragging = null;

      if (layer.hasPointerCapture(event.pointerId)) {
        layer.releasePointerCapture(event.pointerId);
      }

      const minimum = step === "issue"
        ? { minWidth: 15, minHeight: 15 }
        : { minWidth: 130, minHeight: 80 };

      if (!validBox(box, minimum)) {
        message.textContent = step === "issue"
          ? "Область ошибки слишком мала. Выделите заново."
          : "Место для карточки слишком мало. Выделите область побольше.";

        (step === "issue" ? draftIssue : draftCallout).hidden = true;

        return;
      }

      if (step === "issue") {
        issue = box;

        positionBox(draftIssue, box);
        draftIssue.hidden = false;

        setMode(
          "callout",
          "Теперь обведите место для карточки текста на ЭТОМ ЖЕ листе.",
        );

      } else {
        callout = box;

        positionBox(draftCallout, box);
        draftCallout.hidden = false;

        draftLine = createManualLine(issue, callout);
        layer.append(draftLine);
        if (geometryNote) {
          setMode("confirm", "Проверьте новые области и нажмите «Сохранить области».");
          saveGeometry.focus();
        } else {
          setMode("editing", "Введите замечание и нормативное основание.");
          editor.open();
        }
      }
    });

    listen(layer, "pointercancel", () => {
      if (!dragging) {
        return;
      }

      const step = dragging.step;
      dragging = null;

      (step === "issue" ? draftIssue : draftCallout).hidden = true;

      message.textContent = (
        "Выделение прервано. Повторите попытку."
      );
    });

    syncSize();

    image.addEventListener("load", syncSize);

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(syncSize);

      observer.observe(image);
      disposeHandlers.push(() => observer.disconnect());

    } else {
      window.addEventListener("resize", syncSize);

      disposeHandlers.push(
        () => window.removeEventListener("resize", syncSize),
      );
    }

    disposeHandlers.push(
      () => image.removeEventListener("load", syncSize),
    );
  }

  function mount(visualization, root) {
    dispose();

    const pages = visualization.querySelectorAll(PAGE_SELECTOR);

    pages.forEach(
      (page) => mountPage(page, root),
    );

    return pages.length;
  }

  return { mount, dispose };
}
