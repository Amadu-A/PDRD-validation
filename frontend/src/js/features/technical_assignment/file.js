// frontend/src/js/features/technical_assignment/file.js

/**
 * Управляет optional upload технического задания.
 */

import {
  requireElementWithin,
} from "../../dom.js";


const TECHNICAL_ASSIGNMENT_ACCEPT = (
  ".pdf,.doc,.docx,"
  + "application/pdf,"
  + "application/msword,"
  + "application/vnd.openxmlformats-officedocument."
  + "wordprocessingml.document"
);


export function createTechnicalAssignmentFilePicker(
  root,
) {
  const section = requireElementWithin(
    root,
    "[data-specification-placeholder]",
  );

  const input = requireElementWithin(
    section,
    "#specificationFile",
  );

  const disabledSurface = requireElementWithin(
    section,
    ".normative-sidebar__disabled-surface",
  );

  const selectLabel = requireElementWithin(
    section,
    "label[for=\"specificationFile\"]",
  );

  const fileName = requireElementWithin(
    section,
    ".normative-sidebar__file-picker-name",
  );

  const badge = requireElementWithin(
    section,
    ".normative-sidebar__disabled-badge",
  );

  const hint = requireElementWithin(
    section,
    ".normative-sidebar__block-hint",
  );


  function hasFile() {
    return Boolean(
      input.files[0],
    );
  }


  function clear() {
    input.value = "";

    sync();
  }


  function sync() {
    const file = input.files[0];

    fileName.textContent = (
      file
        ? file.name
        : "Файл не выбран"
    );

    if (file) {
      badge.textContent = "Очистить";

      badge.setAttribute(
        "role",
        "button",
      );

      badge.setAttribute(
        "tabindex",
        "0",
      );

      badge.dataset.action = "clear";

      return;
    }

    badge.textContent = "Необязательно";

    badge.removeAttribute(
      "role",
    );

    badge.removeAttribute(
      "tabindex",
    );

    delete badge.dataset.action;
  }


  function activate() {
    section.removeAttribute(
      "aria-disabled",
    );

    section.classList.remove(
      "normative-sidebar__specification",
    );

    disabledSurface.classList.remove(
      "normative-sidebar__disabled-surface",
    );

    selectLabel.classList.add(
      "normative-sidebar__button",
    );

    selectLabel.setAttribute(
      "role",
      "button",
    );

    selectLabel.setAttribute(
      "tabindex",
      "0",
    );

    input.disabled = false;

    input.accept = (
      TECHNICAL_ASSIGNMENT_ACCEPT
    );

    hint.textContent = (
      "PDF, DOC или DOCX. "
      + "ТЗ относится только к текущему анализу."
    );

    sync();
  }


  function handleSelectKeydown(
    event,
  ) {
    if (
      event.key !== "Enter"
      && event.key !== " "
    ) {
      return;
    }

    event.preventDefault();

    input.click();
  }


  function handleBadgeClick() {
    if (!hasFile()) {
      return;
    }

    clear();
  }


  function handleBadgeKeydown(
    event,
  ) {
    if (
      !hasFile()
      || (
        event.key !== "Enter"
        && event.key !== " "
      )
    ) {
      return;
    }

    event.preventDefault();

    clear();
  }


  function bind() {
    activate();

    input.addEventListener(
      "change",
      sync,
    );

    selectLabel.addEventListener(
      "keydown",
      handleSelectKeydown,
    );

    badge.addEventListener(
      "click",
      handleBadgeClick,
    );

    badge.addEventListener(
      "keydown",
      handleBadgeKeydown,
    );
  }


  return {
    bind,
    input,
  };
}