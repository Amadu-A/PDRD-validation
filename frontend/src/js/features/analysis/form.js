// frontend/src/js/features/analysis/form.js

/**
 * Состояние, валидация и serialization формы анализа.
 */

import {
  EXPLANATORY_NOTE_ENABLED,
} from "../../config.js";


const TECHNICAL_ASSIGNMENT_FILE_PATTERN = (
  /\.(?:pdf|doc|docx)$/i
);


const PROJECT_CONTEXT_KIND_LABELS = {
  drawing: "чертёж",
  specification: "спецификация",
  other: "другой тип документа",
};


export function createAnalysisForm({
  formElement,
  pdfInput,
  cadInput,
  technicalAssignmentInput,
  pagesInput,
  pagesHint,
  useExplanatoryNoteInput,
  noteStartPageInput,
  noteEndPageInput,
  getNormativeSelection = () => null,
}) {
  let projectContextPreflightState = null;

  const projectContextFeedback = (
    createProjectContextFeedback()
  );


  function createProjectContextFeedback() {
    const startField = (
      noteStartPageInput.parentElement
    );

    const noteRange = (
      startField?.parentElement
    );

    if (!noteRange) {
      throw new Error(
        "Не найден контейнер диапазона ПЗ.",
      );
    }

    const root = document.createElement(
      "div",
    );

    root.className = (
      "analysis-form__context-feedback"
    );

    root.dataset.state = "warning";

    root.hidden = true;

    root.setAttribute(
      "role",
      "status",
    );

    root.setAttribute(
      "aria-live",
      "polite",
    );

    const title = document.createElement(
      "strong",
    );

    title.className = (
      "analysis-form__context-feedback-title"
    );

    const summary = document.createElement(
      "p",
    );

    summary.className = (
      "analysis-form__context-feedback-text"
    );

    const list = document.createElement(
      "ul",
    );

    list.className = (
      "analysis-form__context-feedback-list"
    );

    const hint = document.createElement(
      "p",
    );

    hint.className = (
      "analysis-form__context-feedback-hint"
    );

    const actions = document.createElement(
      "div",
    );

    actions.className = (
      "analysis-form__context-feedback-actions"
    );

    const changeButton = document.createElement(
      "button",
    );

    changeButton.className = (
      "analysis-form__context-feedback-button"
    );

    changeButton.type = "button";

    changeButton.textContent = "Изменить диапазон";

    const confirmButton = document.createElement(
      "button",
    );

    confirmButton.className = (
      "analysis-form__context-feedback-button "
      + "analysis-form__context-feedback-button--primary"
    );

    confirmButton.type = "button";

    confirmButton.textContent = (
      "Всё верно — продолжить анализ"
    );

    actions.append(
      changeButton,
      confirmButton,
    );

    root.append(
      title,
      summary,
      list,
      hint,
      actions,
    );

    noteRange.prepend(
      root,
    );

    return {
      root,
      title,
      summary,
      list,
      hint,
      actions,
      changeButton,
      confirmButton,
    };
  }


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


  function isProjectContextEnabled() {
    return (
      EXPLANATORY_NOTE_ENABLED
      && useExplanatoryNoteInput.checked
      && !useExplanatoryNoteInput.disabled
      && hasPdf()
    );
  }


  function projectContextFingerprint() {
    if (!isProjectContextEnabled()) {
      return null;
    }

    const pdf = pdfInput.files[0];

    if (!pdf) {
      return null;
    }

    return [
      pdf.name,
      pdf.size,
      pdf.lastModified,
      noteStartPageInput.value.trim(),
      noteEndPageInput.value.trim(),
    ].join(
      ":",
    );
  }


  function hideProjectContextFeedback() {
    projectContextFeedback.root.hidden = true;

    projectContextFeedback.list.replaceChildren();
  }


  function resetProjectContextPreflight() {
    projectContextPreflightState = null;

    hideProjectContextFeedback();
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

    if (!enabled) {
      resetProjectContextPreflight();
    }
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

    const value = (
      pagesInput.value.trim()
    );

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


  function validateTechnicalAssignment() {
    const file = (
      technicalAssignmentInput.files[0]
    );

    if (!file) {
      return {
        valid: true,

        message: null,
      };
    }

    if (
      !TECHNICAL_ASSIGNMENT_FILE_PATTERN.test(
        file.name,
      )
    ) {
      return {
        valid: false,

        message: (
          "ТЗ поддерживает только PDF, DOC или DOCX."
        ),
      };
    }

    const selection = (
      getNormativeSelection()
    );

    if (!selection) {
      return {
        valid: false,

        message: (
          "Для использования ТЗ выберите "
          + "нормативный раздел."
        ),
      };
    }

    const indexStatus = (
      technicalAssignmentInput.dataset.indexStatus
      ?? ""
    );

    if (indexStatus !== "ready") {
      return {
        valid: false,

        message: (
          "Подождите, пока техническое задание "
          + "будет проиндексировано."
        ),
      };
    }

    const technicalAssignmentId = (
      technicalAssignmentInput.dataset.technicalAssignmentId
      ?? ""
    );

    const analysisDocumentId = (
      technicalAssignmentInput.dataset.analysisDocumentId
      ?? ""
    );

    const preparedSectionId = (
      technicalAssignmentInput.dataset.preparedSectionId
      ?? ""
    );

    if (
      !technicalAssignmentId
      || !analysisDocumentId
    ) {
      return {
        valid: false,

        message: (
          "ТЗ имеет статус «Готов», "
          + "но preflight identity отсутствует."
        ),
      };
    }

    if (
      preparedSectionId
      !== selection.sectionId
    ) {
      return {
        valid: false,

        message: (
          "ТЗ было проиндексировано для другого раздела. "
          + "Дождитесь повторной индексации."
        ),
      };
    }

    return {
      valid: true,

      message: null,
    };
  }


  function validate() {
    const mode = getMode();

    pagesInput.setCustomValidity("");

    noteStartPageInput.setCustomValidity("");

    noteEndPageInput.setCustomValidity("");

    if (mode === "empty") {
      return {
        valid: false,

        message: (
          "Загрузите PDF и/или DWG/DXF."
        ),
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

    const technicalAssignmentValidation = (
      validateTechnicalAssignment()
    );

    if (
      !technicalAssignmentValidation.valid
    ) {
      return technicalAssignmentValidation;
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
      "user_package_document_ids",
      JSON.stringify(
        selection.userPackageDocumentIds
        ?? [],
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


  function appendTechnicalAssignment(
    body,
  ) {
    const technicalAssignment = (
      technicalAssignmentInput.files[0]
    );

    if (!technicalAssignment) {
      return;
    }

    body.append(
      "technical_assignment",
      technicalAssignment,
    );

    body.append(
      "technical_assignment_id",
      technicalAssignmentInput.dataset.technicalAssignmentId,
    );

    body.append(
      "technical_assignment_analysis_document_id",
      technicalAssignmentInput.dataset.analysisDocumentId,
    );
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

    appendTechnicalAssignment(
      body,
    );

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


  function toProjectContextPreflightFormData() {
    if (!isProjectContextEnabled()) {
      throw new Error(
        "Preflight ПЗ запрошен для выключенного контекста.",
      );
    }

    const pdf = pdfInput.files[0];

    if (!pdf) {
      throw new Error(
        "Для preflight ПЗ отсутствует PDF.",
      );
    }

    const body = new FormData();

    body.append(
      "pdf",
      pdf,
    );

    body.append(
      "note_start_page",
      noteStartPageInput.value.trim(),
    );

    body.append(
      "note_end_page",
      noteEndPageInput.value.trim(),
    );

    return body;
  }


  function needsProjectContextPreflight() {
    if (!isProjectContextEnabled()) {
      return false;
    }

    const fingerprint = (
      projectContextFingerprint()
    );

    if (!fingerprint) {
      return true;
    }

    return (
      projectContextPreflightState?.fingerprint
      !== fingerprint
      || (
        projectContextPreflightState?.accepted
        !== true
      )
    );
  }


  function isProjectContextConfirmationPending() {
    const fingerprint = (
      projectContextFingerprint()
    );

    return Boolean(
      fingerprint
      && projectContextPreflightState
      && (
        projectContextPreflightState.fingerprint
        === fingerprint
      )
      && (
        projectContextPreflightState.requiresConfirmation
        === true
      )
      && (
        projectContextPreflightState.accepted
        !== true
      ),
    );
  }


  function applyProjectContextPreflight(
    payload,
  ) {
    const fingerprint = (
      projectContextFingerprint()
    );

    if (!fingerprint) {
      throw new Error(
        "Не удалось определить fingerprint выбранной ПЗ.",
      );
    }

    const warnings = (
      Array.isArray(
        payload?.warnings,
      )
        ? payload.warnings
        : []
    );

    const requiresConfirmation = Boolean(
      payload?.requires_confirmation
      && warnings.length > 0,
    );

    projectContextPreflightState = {
      fingerprint,
      accepted: !requiresConfirmation,
      requiresConfirmation,
      warnings,
      cacheHit: Boolean(
        payload?.cache_hit,
      ),
    };

    if (!requiresConfirmation) {
      hideProjectContextFeedback();

      return true;
    }

    renderProjectContextWarnings(
      warnings,
    );

    return false;
  }


  function renderProjectContextWarnings(
    warnings,
  ) {
    projectContextFeedback.root.dataset.state = (
      "warning"
    );

    projectContextFeedback.title.textContent = (
      "Проверьте выбранные страницы ПЗ"
    );

    projectContextFeedback.summary.textContent = (
      "Автоматическая проверка считает, что некоторые страницы "
      + "могут относиться к другому типу документа."
    );

    projectContextFeedback.list.replaceChildren();

    for (const warning of warnings) {
      const item = document.createElement(
        "li",
      );

      const kind = (
        PROJECT_CONTEXT_KIND_LABELS[
          warning?.kind
        ]
        ?? "другой тип документа"
      );

      const confidence = Math.round(
        Number(
          warning?.confidence
          ?? 0,
        ) * 100,
      );

      const reason = String(
        warning?.reason
        ?? "",
      ).trim();

      item.textContent = (
        `Стр. ${warning?.page_number ?? "?"} — ${kind}`
        + `, уверенность ${confidence}%`
        + (
          reason
            ? `: ${reason}`
            : "."
        )
      );

      projectContextFeedback.list.append(
        item,
      );
    }

    projectContextFeedback.hint.textContent = (
      "Автоматическая классификация может ошибаться. "
      + "Измените диапазон либо подтвердите, что выбранные "
      + "страницы действительно относятся к пояснительной записке."
    );

    projectContextFeedback.actions.hidden = false;

    projectContextFeedback.changeButton.hidden = false;

    projectContextFeedback.confirmButton.hidden = false;

    projectContextFeedback.root.hidden = false;
  }


  function showProjectContextValidationError(
    message,
  ) {
    resetProjectContextPreflight();

    projectContextFeedback.root.dataset.state = (
      "error"
    );

    projectContextFeedback.title.textContent = (
      "Проверьте диапазон пояснительной записки"
    );

    projectContextFeedback.summary.textContent = (
      String(
        message
        || "Не удалось проверить выбранные страницы ПЗ.",
      )
    );

    projectContextFeedback.list.replaceChildren();

    projectContextFeedback.hint.textContent = (
      "Исправьте диапазон и повторите запуск анализа."
    );

    projectContextFeedback.actions.hidden = false;

    projectContextFeedback.changeButton.hidden = false;

    projectContextFeedback.confirmButton.hidden = true;

    projectContextFeedback.root.hidden = false;
  }


  function confirmProjectContextPreflight() {
    const fingerprint = (
      projectContextFingerprint()
    );

    if (
      !fingerprint
      || !projectContextPreflightState
      || (
        projectContextPreflightState.fingerprint
        !== fingerprint
      )
      || (
        projectContextPreflightState.requiresConfirmation
        !== true
      )
    ) {
      resetProjectContextPreflight();

      return false;
    }

    projectContextPreflightState.accepted = true;

    hideProjectContextFeedback();

    return true;
  }


  function focusProjectContextRange() {
    projectContextPreflightState = null;

    hideProjectContextFeedback();

    noteStartPageInput.focus();
  }


  function bind() {
    pdfInput.addEventListener(
      "change",
      () => {
        resetProjectContextPreflight();

        sync();
      },
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
      () => {
        resetProjectContextPreflight();

        syncExplanatoryNote();
      },
    );

    noteStartPageInput.addEventListener(
      "input",
      () => {
        noteStartPageInput.setCustomValidity("");

        resetProjectContextPreflight();
      },
    );

    noteEndPageInput.addEventListener(
      "input",
      () => {
        noteEndPageInput.setCustomValidity("");

        resetProjectContextPreflight();
      },
    );

    projectContextFeedback.changeButton.addEventListener(
      "click",
      focusProjectContextRange,
    );

    projectContextFeedback.confirmButton.addEventListener(
      "click",
      () => {
        if (
          confirmProjectContextPreflight()
        ) {
          formElement.requestSubmit();
        }
      },
    );

    sync();
  }


  return {
    applyProjectContextPreflight,
    bind,
    getMode,
    isProjectContextConfirmationPending,
    needsProjectContextPreflight,
    showProjectContextValidationError,
    sync,
    toFormData,
    toProjectContextPreflightFormData,
    validate,
  };
}
