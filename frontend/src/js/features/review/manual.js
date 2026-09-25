// frontend/src/js/features/review/manual.js

/**
 * Ручное создание Gold-находок на одной PDF-странице.
 * Две области выбираются на изображении одного листа; координаты 0..1000.
 * Все операции локальные, серверное сохранение здесь отсутствует.
 */

import {
  boxBetween,
  connectorPoints,
  normalizedPoint,
  positionBox,
  validBox,
} from "./geometry.js";

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

function manualLine(issue, callout) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");

  svg.classList.add("manual-annotation__connector");
  svg.setAttribute("viewBox", "0 0 1000 1000");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");

  const line = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "polyline",
  );

  line.setAttribute("points", connectorPoints(issue, callout));
  svg.append(line);

  return svg;
}

function createEditor(pageNumber, onSave, onCancel) {
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

/**
 * Интегрирует ручные области с уже существующим review controller.
 * Каждый контроллер страницы хранит свою геометрию, поэтому области не могут
 * случайно пересечь границу соседнего листа.
 */
export function createManualAnnotationController({
  onCreate,
  onUpdate,
  onGet,

  idFactory = () => (
    globalThis.crypto?.randomUUID?.()
    ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`
  ),
}) {
  let activeAbort = null;
  let disposeHandlers = [];

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
    }

    function resetDraft() {
      issue = null;
      callout = null;
      dragging = null;

      draftIssue.hidden = true;
      draftCallout.hidden = true;

      editingNote = null;

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

      const region = element(
        "div",
        "manual-annotation__issue",
      );

      positionBox(region, issue);

      region.title = (
        `Область пользовательского замечания на странице ${pageNumber}`
      );

      const line = manualLine(issue, callout);

      const card = element(
        "article",
        "manual-annotation__card",
      );

      card.dataset.findingId = findingId;
      card.dataset.manualPage = String(pageNumber);

      positionBox(card, callout);

      const textNode = element(
        "p",
        "manual-annotation__text",
        text,
      );

      const normNode = element(
        "p",
        "manual-annotation__norm",
      );

      normNode.textContent = normativeSection
        ? `Нормативное основание: ${normativeSection}`
        : "Нормативное основание не указано";

      card.append(textNode, normNode);

      const note = {
        findingId,
        pageNumber,
        issueBox: { ...issue },
        calloutBox: { ...callout },
        textNode,
        normNode,
        card,
      };

      onCreate({
        ...note,
        text,
        normativeSection,

        onEdit: () => {
          editingNote = note;

          const entry = onGet(note.findingId);

          editor.open(
            entry.text,
            entry.normativeSection,
          );
        },
      });

      layer.append(line, region, card);
    }

    const editor = createEditor(
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

    add.addEventListener("click", () => {
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

        return;
      }

      if (activeAbort && activeAbort !== abort) {
        activeAbort();
      }

      activeAbort = abort;
      syncSize();

      setMode(
        "issue",
        "Обведите мышью область ошибки на этом листе. Esc — отмена.",
      );

      layer.focus();
    });

    cancel.addEventListener("click", abort);

    page.addEventListener("keydown", (event) => {
      if (
        event.key === "Escape"
        && (mode === "issue" || mode === "callout")
      ) {
        event.preventDefault();
        abort();
      }
    });

    layer.addEventListener("pointerdown", (event) => {
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

    layer.addEventListener("pointermove", (event) => {
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

    layer.addEventListener("pointerup", (event) => {
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

        setMode(
          "editing",
          "Введите замечание и нормативное основание.",
        );

        editor.open();
      }
    });

    layer.addEventListener("pointercancel", () => {
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
    for (const dispose of disposeHandlers) {
      dispose();
    }

    disposeHandlers = [];

    if (activeAbort) {
      activeAbort();
    }

    activeAbort = null;

    const pages = visualization.querySelectorAll(PAGE_SELECTOR);

    pages.forEach(
      (page) => mountPage(page, root),
    );

    return pages.length;
  }

  return { mount };
}