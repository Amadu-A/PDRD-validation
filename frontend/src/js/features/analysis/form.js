// frontend/src/js/features/analysis/form.js

/**
 * Состояние, валидация и serialization формы анализа.
 */

import {
  EXPLANATORY_NOTE_ENABLED,
} from "../../config.js";


export function createAnalysisForm({
  pdfInput,
  cadInput,
  pagesInput,
  pagesHint,
  useExplanatoryNoteInput,
  noteStartPageInput,
  noteEndPageInput,
  getNormativeSelection = () => null,
}) {
  function hasPdf() {
    return Boolean(
      pdfInput.files[0],
    );
  }


  function hasCad() {
    return Boolean(
      cadInput.files[0],
    );
  }


  function getMode() {
    if (
      hasPdf()
      && hasCad()
    ) {
      return "pdf_cad";
    }

    if (hasPdf()) {
      return "pdf_only";
    }

    if (hasCad()) {
      return "cad_only";
    }

    return "empty";
  }


  function syncExplanatoryNote() {
    const mode = getMode();

    const available = (
      EXPLANATORY_NOTE_ENABLED
      && mode !== "cad_only"
      && mode !== "empty"
    );

    useExplanatoryNoteInput.disabled = (
      !available
    );

    if (!available) {
      useExplanatoryNoteInput.checked = false;
    }

    const enabled = (
      available
      && useExplanatoryNoteInput.checked
    );

    noteStartPageInput.disabled = !enabled;

    noteEndPageInput.disabled = !enabled;

    noteStartPageInput.required = enabled;

    noteEndPageInput.required = enabled;
  }


  function sync() {
    const mode = getMode();

    pagesInput.setCustomValidity("");

    if (mode === "cad_only") {
      pagesInput.value = "";

      pagesInput.disabled = true;

      pagesInput.required = false;

      pagesInput.placeholder = (
        "Для CAD-only не используется"
      );

      pagesHint.textContent = (
        "CAD-only: DWG/DXF считается одним листом."
      );

    } else if (mode === "pdf_cad") {
      pagesInput.disabled = false;

      pagesInput.required = true;

      pagesInput.placeholder = (
        "Например: 11"
      );

      pagesHint.textContent = (
        "PDF + CAD: укажите ровно одну страницу PDF, "
        + "соответствующую загруженному DWG/DXF."
      );

    } else {
      pagesInput.disabled = false;

      pagesInput.required = false;

      pagesInput.placeholder = (
        "PDF-only: 3,5,8-12. Пусто = весь PDF"
      );

      pagesHint.textContent = (
        "Для PDF-only можно анализировать "
        + "одну или несколько страниц."
      );
    }

    syncExplanatoryNote();
  }


  function validatePdfCadPage(
    mode,
  ) {
    if (mode !== "pdf_cad") {
      return true;
    }

    const value = pagesInput.value.trim();

    if (/^[1-9]\d*$/.test(value)) {
      return true;
    }

    pagesInput.setCustomValidity(
      "При PDF + CAD укажите ровно одну "
      + "положительную страницу PDF.",
    );

    pagesInput.reportValidity();

    return false;
  }


  function validateExplanatoryNote() {
    if (
      !EXPLANATORY_NOTE_ENABLED
      || !useExplanatoryNoteInput.checked
    ) {
      return true;
    }

    const start = Number(
      noteStartPageInput.value,
    );

    const end = Number(
      noteEndPageInput.value,
    );

    if (
      !Number.isInteger(
        start,
      )
      || start < 1
    ) {
      noteStartPageInput.setCustomValidity(
        "Начальная страница ПЗ должна быть "
        + "положительным целым числом.",
      );

      noteStartPageInput.reportValidity();

      return false;
    }

    if (
      !Number.isInteger(
        end,
      )
      || end < 1
    ) {
      noteEndPageInput.setCustomValidity(
        "Конечная страница ПЗ должна быть "
        + "положительным целым числом.",
      );

      noteEndPageInput.reportValidity();

      return false;
    }

    if (end <= start) {
      noteEndPageInput.setCustomValidity(
        "Конечная страница ПЗ должна быть "
        + "больше начальной.",
      );

      noteEndPageInput.reportValidity();

      return false;
    }

    return true;
  }


  function validate() {
    const mode = getMode();

    pagesInput.setCustomValidity("");

    noteStartPageInput.setCustomValidity("");

    noteEndPageInput.setCustomValidity("");

    if (mode === "empty") {
      return {
        valid: false,

        message: "Загрузите PDF и/или DWG/DXF.",
      };
    }

    if (
      !validatePdfCadPage(
        mode,
      )
    ) {
      return {
        valid: false,

        message: null,
      };
    }

    if (
      !validateExplanatoryNote()
    ) {
      return {
        valid: false,

        message: null,
      };
    }

    return {
      valid: true,

      message: null,
    };
  }


  function appendNormativeSelection(
    body,
  ) {
    const selection = (
      getNormativeSelection()
    );

    if (!selection) {
      return;
    }

    body.append(
      "normative_section_id",
      selection.sectionId,
    );

    body.append(
      "normative_document_ids",
      JSON.stringify(
        selection.documentIds,
      ),
    );

    body.append(
      "normative_prompt_override_enabled",
      (
        selection.promptOverrideEnabled
          ? "true"
          : "false"
      ),
    );

    if (
      selection.promptOverrideEnabled
    ) {
      body.append(
        "normative_prompt_override",
        selection.promptOverride ?? "",
      );
    }
  }


  function toFormData() {
    const body = new FormData();

    const pdf = pdfInput.files[0];

    const cad = cadInput.files[0];

    if (pdf) {
      body.append(
        "pdf",
        pdf,
      );
    }

    if (cad) {
      body.append(
        "cad",
        cad,
      );
    }

    if (
      !pagesInput.disabled
      && pagesInput.value.trim()
    ) {
      body.append(
        "pages",
        pagesInput.value.trim(),
      );
    }

    if (
      EXPLANATORY_NOTE_ENABLED
      && useExplanatoryNoteInput.checked
    ) {
      body.append(
        "use_explanatory_note",
        "true",
      );

      body.append(
        "note_start_page",
        noteStartPageInput.value.trim(),
      );

      body.append(
        "note_end_page",
        noteEndPageInput.value.trim(),
      );
    }

    appendNormativeSelection(
      body,
    );

    return body;
  }


  function bind() {
    pdfInput.addEventListener(
      "change",
      sync,
    );

    cadInput.addEventListener(
      "change",
      sync,
    );

    pagesInput.addEventListener(
      "input",
      () => {
        pagesInput.setCustomValidity("");
      },
    );

    useExplanatoryNoteInput.addEventListener(
      "change",
      syncExplanatoryNote,
    );

    noteStartPageInput.addEventListener(
      "input",
      () => {
        noteStartPageInput.setCustomValidity("");
      },
    );

    noteEndPageInput.addEventListener(
      "input",
      () => {
        noteEndPageInput.setCustomValidity("");
      },
    );

    sync();
  }


  return {
    bind,
    getMode,
    sync,
    toFormData,
    validate,
  };
}