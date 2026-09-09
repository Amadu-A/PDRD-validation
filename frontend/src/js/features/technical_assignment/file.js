// frontend/src/js/features/technical_assignment/file.js

/**
 * Управляет preflight upload и indexing lifecycle ТЗ.
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

const TECHNICAL_ASSIGNMENT_ENDPOINT = (
  "/api/v1/normative/technical-assignments"
);

const POLL_INTERVAL_MS = 2000;

const STATUS_LABELS = {
  waiting_section: "Ожидает раздел",
  uploading: "Загрузка",
  uploaded: "Загружен",
  queued: "В очереди",
  indexing: "Индексируется",
  ready: "Готов",
  failed: "Ошибка",
};


async function extractErrorMessage(
  response,
) {
  try {
    const payload = await response.json();

    if (
      payload
      && typeof payload.detail === "string"
    ) {
      return payload.detail;
    }

    if (payload?.detail) {
      return JSON.stringify(
        payload.detail,
      );
    }

  } catch {
    // Используем HTTP status ниже.
  }

  return `HTTP ${response.status}`;
}


async function requestJson(
  url,
  options = {},
) {
  const response = await fetch(
    url,
    options,
  );

  if (!response.ok) {
    throw new Error(
      await extractErrorMessage(
        response,
      ),
    );
  }

  return response.json();
}


export function createTechnicalAssignmentFilePicker(
  root,
  {
    submitButton = null,
  } = {},
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

  const statusBadge = document.createElement(
    "span",
  );

  statusBadge.className = (
    "normative-sidebar__status-badge"
  );

  statusBadge.hidden = true;

  fileName.insertAdjacentElement(
    "afterend",
    statusBadge,
  );


  const state = {
    sectionId: null,

    status: "empty",

    requestVersion: 0,

    pollTimer: null,

    indexError: null,
  };


  function hasFile() {
    return Boolean(
      input.files[0],
    );
  }


  function clearPoll() {
    if (state.pollTimer === null) {
      return;
    }

    window.clearTimeout(
      state.pollTimer,
    );

    state.pollTimer = null;
  }


  function clearPreparedIdentity() {
    delete input.dataset.technicalAssignmentId;

    delete input.dataset.analysisDocumentId;

    delete input.dataset.preparedSectionId;

    delete input.dataset.indexStatus;
  }


  function setPreparedIdentity(
    payload,
  ) {
    input.dataset.technicalAssignmentId = (
      payload.technical_assignment_id
    );

    input.dataset.analysisDocumentId = (
      payload.analysis_document_id
    );

    input.dataset.preparedSectionId = (
      payload.section_id
    );
  }


  function submitGuardMessage() {
    if (!hasFile()) {
      return "";
    }

    if (!state.sectionId) {
      return (
        "Выберите нормативный раздел, "
        + "чтобы подготовить ТЗ."
      );
    }

    if (state.status === "ready") {
      return "";
    }

    if (state.status === "failed") {
      return (
        "ТЗ не проиндексировано. "
        + "Исправьте ошибку или выберите файл заново."
      );
    }

    return (
      "Подождите, пока техническое задание "
      + "будет проиндексировано."
    );
  }


  function syncSubmitGuard() {
    if (!submitButton) {
      return;
    }

    const blocked = (
      hasFile()
      && state.status !== "ready"
    );

    submitButton.disabled = blocked;

    const message = (
      blocked
        ? submitGuardMessage()
        : ""
    );

    submitButton.title = message;

    const parent = submitButton.parentElement;

    if (parent) {
      parent.title = message;
    }
  }


  function visualStatus(
    statusName,
  ) {
    if (
      statusName === "uploading"
      || statusName === "waiting_section"
    ) {
      return "queued";
    }

    return statusName;
  }


  function setStatus(
    statusName,
    {
      error = null,
    } = {},
  ) {
    state.status = statusName;

    state.indexError = error;

    input.dataset.indexStatus = statusName;

    if (
      statusName === "empty"
    ) {
      statusBadge.hidden = true;

    } else {
      statusBadge.hidden = false;

      statusBadge.dataset.status = (
        visualStatus(
          statusName,
        )
      );

      statusBadge.textContent = (
        STATUS_LABELS[statusName]
        || statusName
      );
    }

    if (
      statusName === "failed"
      && error
    ) {
      statusBadge.title = error;

      hint.textContent = (
        `Ошибка индексации ТЗ: ${error}`
      );

    } else if (
      statusName === "ready"
    ) {
      statusBadge.title = (
        "Техническое задание готово к анализу."
      );

      hint.textContent = (
        "ТЗ проиндексировано и готово к анализу."
      );

    } else if (
      statusName === "waiting_section"
    ) {
      statusBadge.title = (
        "Для индексации ТЗ выберите раздел."
      );

      hint.textContent = (
        "Выберите нормативный раздел. "
        + "После этого ТЗ будет проиндексировано автоматически."
      );

    } else if (
      statusName !== "empty"
    ) {
      statusBadge.title = (
        "Подготовка технического задания."
      );

      hint.textContent = (
        "ТЗ индексируется. "
        + "Анализ станет доступен после статуса «Готов»."
      );

    } else {
      statusBadge.removeAttribute(
        "title",
      );

      hint.textContent = (
        "PDF, DOC или DOCX. "
        + "ТЗ относится только к текущему анализу."
      );
    }

    syncSubmitGuard();
  }


  function schedulePoll(
    requestVersion,
  ) {
    clearPoll();

    state.pollTimer = window.setTimeout(
      () => {
        void pollStatus(
          requestVersion,
        );
      },
      POLL_INTERVAL_MS,
    );
  }


  async function pollStatus(
    requestVersion,
  ) {
    if (
      requestVersion
      !== state.requestVersion
    ) {
      return;
    }

    const technicalAssignmentId = (
      input.dataset.technicalAssignmentId
    );

    if (!technicalAssignmentId) {
      return;
    }

    try {
      const payload = await requestJson(
        (
          `${TECHNICAL_ASSIGNMENT_ENDPOINT}/`
          + `${encodeURIComponent(technicalAssignmentId)}`
          + "/status"
        ),
      );

      if (
        requestVersion
        !== state.requestVersion
      ) {
        return;
      }

      setStatus(
        payload.index_status,
        {
          error: (
            payload.index_error
            ?? null
          ),
        },
      );

      if (
        payload.index_status === "ready"
        || payload.index_status === "failed"
      ) {
        return;
      }

      schedulePoll(
        requestVersion,
      );

    } catch (error) {
      if (
        requestVersion
        !== state.requestVersion
      ) {
        return;
      }

      setStatus(
        "failed",
        {
          error: (
            error instanceof Error
              ? error.message
              : String(error)
          ),
        },
      );
    }
  }


  async function prepareCurrentFile() {
    clearPoll();

    state.requestVersion += 1;

    const requestVersion = (
      state.requestVersion
    );

    clearPreparedIdentity();

    const file = input.files[0];

    if (!file) {
      setStatus(
        "empty",
      );

      return;
    }

    if (!state.sectionId) {
      setStatus(
        "waiting_section",
      );

      return;
    }

    setStatus(
      "uploading",
    );

    const body = new FormData();

    body.append(
      "file",
      file,
    );

    body.append(
      "section_id",
      state.sectionId,
    );

    try {
      const payload = await requestJson(
        `${TECHNICAL_ASSIGNMENT_ENDPOINT}/prepare`,
        {
          method: "POST",
          body,
        },
      );

      if (
        requestVersion
        !== state.requestVersion
      ) {
        return;
      }

      setPreparedIdentity(
        payload,
      );

      setStatus(
        payload.index_status,
        {
          error: (
            payload.index_error
            ?? null
          ),
        },
      );

      if (
        payload.index_status !== "ready"
        && payload.index_status !== "failed"
      ) {
        schedulePoll(
          requestVersion,
        );
      }

    } catch (error) {
      if (
        requestVersion
        !== state.requestVersion
      ) {
        return;
      }

      setStatus(
        "failed",
        {
          error: (
            error instanceof Error
              ? error.message
              : String(error)
          ),
        },
      );
    }
  }


  function clear() {
    clearPoll();

    state.requestVersion += 1;

    state.indexError = null;

    input.value = "";

    clearPreparedIdentity();

    sync();

    setStatus(
      "empty",
    );
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
      + "После выбора ТЗ индексируется автоматически."
    );

    sync();
  }


  async function setSection(
    sectionId,
  ) {
    const normalized = (
      sectionId
        ? String(sectionId)
        : null
    );

    if (
      normalized
      === state.sectionId
    ) {
      return;
    }

    state.sectionId = normalized;

    if (!hasFile()) {
      return;
    }

    await prepareCurrentFile();
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
      () => {
        sync();

        void prepareCurrentFile();
      },
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

    setStatus(
      "empty",
    );
  }


  return {
    bind,
    input,
    setSection,
  };
}