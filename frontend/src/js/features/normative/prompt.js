// frontend/src/js/features/normative/prompt.js

/**
 * Working/system prompt editor normative section.
 */

import {
  requireElementWithin,
} from "../../dom.js";

import {
  getSection,
  updateSection,
} from "./api.js";


export function createNormativePromptEditor(
  root,
) {
  const textarea = requireElementWithin(
    root,
    "[data-normative-prompt]",
  );

  const saveButton = requireElementWithin(
    root,
    "[data-normative-prompt-save]",
  );

  const restoreButton = requireElementWithin(
    root,
    "[data-normative-prompt-restore]",
  );

  const statusElement = requireElementWithin(
    root,
    "[data-normative-prompt-status]",
  );

  const openButton = requireElementWithin(
    root,
    "[data-normative-prompt-open]",
  );

  const dialog = requireElementWithin(
    root,
    "[data-normative-prompt-dialog]",
  );

  const dialogTextarea = requireElementWithin(
    dialog,
    "[data-normative-prompt-dialog-textarea]",
  );

  const dialogSaveButton = requireElementWithin(
    dialog,
    "[data-normative-prompt-dialog-save]",
  );

  const dialogRestoreButton = requireElementWithin(
    dialog,
    "[data-normative-prompt-dialog-restore]",
  );

  const dialogStatusElement = requireElementWithin(
    dialog,
    "[data-normative-prompt-dialog-status]",
  );

  const dialogCloseButtons = dialog.querySelectorAll(
    "[data-normative-prompt-dialog-close]",
  );


  const state = {
    sectionId: null,

    readySectionId: null,

    workingBySection: new Map(),

    systemBySection: new Map(),

    dirtySections: new Set(),

    requestToken: 0,
  };


  function setStatus(
    message,
    stateName = "normal",
  ) {
    statusElement.textContent = message;

    statusElement.dataset.state = stateName;

    dialogStatusElement.textContent = message;

    dialogStatusElement.dataset.state = stateName;
  }


  function setDisabled(
    disabled,
  ) {
    textarea.disabled = disabled;

    saveButton.disabled = disabled;

    restoreButton.disabled = disabled;

    dialogTextarea.disabled = disabled;

    dialogSaveButton.disabled = disabled;

    dialogRestoreButton.disabled = disabled;
  }


  function syncPromptValues(
    value,
    source = null,
  ) {
    if (
      source !== textarea
      && textarea.value !== value
    ) {
      textarea.value = value;
    }

    if (
      source !== dialogTextarea
      && dialogTextarea.value !== value
    ) {
      dialogTextarea.value = value;
    }
  }


  function currentWorkingPrompt() {
    if (!state.sectionId) {
      return "";
    }

    return (
      state.workingBySection.get(
        state.sectionId,
      )
      ?? ""
    );
  }


  function currentSystemPrompt() {
    if (!state.sectionId) {
      return "";
    }

    return (
      state.systemBySection.get(
        state.sectionId,
      )
      ?? ""
    );
  }


  function isCurrentSectionDirty() {
    return Boolean(
      state.sectionId
      && state.dirtySections.has(
        state.sectionId,
      )
    );
  }


  function renderDirtyState() {
    if (!state.sectionId) {
      setStatus(
        "Выберите нормативный раздел.",
      );

      return;
    }

    if (!isCurrentSectionDirty()) {
      setStatus(
        "Используется сохранённый системный prompt.",
      );

      return;
    }

    setStatus(
      "Рабочий prompt изменён, но не сохранён.",
      "dirty",
    );
  }


  function updateDirtyState(
    sectionId,
  ) {
    const workingPrompt = (
      state.workingBySection.get(
        sectionId,
      )
      ?? ""
    );

    const systemPrompt = (
      state.systemBySection.get(
        sectionId,
      )
      ?? ""
    );

    if (workingPrompt === systemPrompt) {
      state.dirtySections.delete(
        sectionId,
      );

      return;
    }

    state.dirtySections.add(
      sectionId,
    );
  }


  function updateWorkingPrompt(
    source,
  ) {
    if (!state.sectionId) {
      return;
    }

    const sectionId = state.sectionId;

    state.workingBySection.set(
      sectionId,
      source.value,
    );

    updateDirtyState(
      sectionId,
    );

    syncPromptValues(
      source.value,
      source,
    );

    renderDirtyState();
  }


  function openDialog() {
    syncPromptValues(
      textarea.value,
    );

    if (dialog.open) {
      return;
    }

    if (
      typeof dialog.showModal === "function"
    ) {
      dialog.showModal();

    } else {
      dialog.setAttribute(
        "open",
        "",
      );
    }

    window.requestAnimationFrame(
      () => {
        if (!dialogTextarea.disabled) {
          dialogTextarea.focus();
        }
      },
    );
  }


  function closeDialog() {
    if (!dialog.open) {
      return;
    }

    if (
      typeof dialog.close === "function"
    ) {
      dialog.close();

    } else {
      dialog.removeAttribute(
        "open",
      );
    }
  }


  async function loadSection(
    sectionId,
    {
      replaceWorking = false,
    } = {},
  ) {
    const token = (
      state.requestToken
      + 1
    );

    state.requestToken = token;

    state.sectionId = sectionId;

    state.readySectionId = null;

    if (!sectionId) {
      syncPromptValues(
        "",
      );

      setDisabled(
        true,
      );

      setStatus(
        "Выберите нормативный раздел.",
      );

      return false;
    }

    setDisabled(
      true,
    );

    setStatus(
      "Загружаем системный prompt…",
    );

    try {
      const section = await getSection(
        sectionId,
      );

      if (
        token
        !== state.requestToken
      ) {
        return false;
      }

      state.systemBySection.set(
        sectionId,
        section.system_prompt,
      );

      if (
        replaceWorking
        || !state.dirtySections.has(
          sectionId,
        )
      ) {
        state.workingBySection.set(
          sectionId,
          section.system_prompt,
        );

        state.dirtySections.delete(
          sectionId,
        );
      } else {
        updateDirtyState(
          sectionId,
        );
      }

      syncPromptValues(
        state.workingBySection.get(
          sectionId,
        )
        ?? "",
      );

      state.readySectionId = sectionId;

      setDisabled(
        false,
      );

      renderDirtyState();

      return true;

    } catch (error) {
      if (
        token
        !== state.requestToken
      ) {
        return false;
      }

      state.readySectionId = null;

      syncPromptValues(
        "",
      );

      setDisabled(
        true,
      );

      setStatus(
        error instanceof Error
          ? error.message
          : String(error),
        "error",
      );

      return false;
    }
  }


  async function saveSystemPrompt() {
    if (
      !state.sectionId
      || state.readySectionId !== state.sectionId
    ) {
      return;
    }

    const sectionId = state.sectionId;

    const workingPrompt = currentWorkingPrompt();

    setDisabled(
      true,
    );

    setStatus(
      "Сохраняем системный prompt…",
    );

    try {
      const section = await updateSection(
        sectionId,
        {
          system_prompt: workingPrompt,
        },
      );

      if (
        state.sectionId
        !== sectionId
      ) {
        return;
      }

      state.systemBySection.set(
        sectionId,
        section.system_prompt,
      );

      state.workingBySection.set(
        sectionId,
        section.system_prompt,
      );

      state.dirtySections.delete(
        sectionId,
      );

      state.readySectionId = sectionId;

      syncPromptValues(
        section.system_prompt,
      );

      setStatus(
        "Системный prompt сохранён.",
      );

    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : String(error),
        "error",
      );

    } finally {
      if (
        state.sectionId
        === sectionId
      ) {
        setDisabled(
          false,
        );
      }
    }
  }


  async function restoreSystemPrompt() {
    if (!state.sectionId) {
      return;
    }

    const sectionId = state.sectionId;

    const restored = await loadSection(
      sectionId,
      {
        replaceWorking: true,
      },
    );

    if (
      restored
      && state.sectionId === sectionId
      && state.readySectionId === sectionId
    ) {
      setStatus(
        "Рабочий prompt восстановлен из системного.",
      );
    }
  }


  textarea.addEventListener(
    "input",
    () => {
      updateWorkingPrompt(
        textarea,
      );
    },
  );


  dialogTextarea.addEventListener(
    "input",
    () => {
      updateWorkingPrompt(
        dialogTextarea,
      );
    },
  );


  saveButton.addEventListener(
    "click",
    async () => {
      await saveSystemPrompt();
    },
  );


  restoreButton.addEventListener(
    "click",
    async () => {
      await restoreSystemPrompt();
    },
  );


  dialogSaveButton.addEventListener(
    "click",
    async () => {
      await saveSystemPrompt();
    },
  );


  dialogRestoreButton.addEventListener(
    "click",
    async () => {
      await restoreSystemPrompt();
    },
  );


  openButton.addEventListener(
    "click",
    openDialog,
  );


  dialogCloseButtons.forEach(
    (button) => {
      button.addEventListener(
        "click",
        closeDialog,
      );
    },
  );


  dialog.addEventListener(
    "click",
    (event) => {
      if (event.target === dialog) {
        closeDialog();
      }
    },
  );


  function getOverride(
    sectionId,
  ) {
    const canUseWorkingPrompt = (
      Boolean(
        sectionId,
      )
      && sectionId === state.sectionId
      && sectionId === state.readySectionId
      && state.workingBySection.has(
        sectionId,
      )
      && state.systemBySection.has(
        sectionId,
      )
    );

    if (
      !canUseWorkingPrompt
      || !state.dirtySections.has(
        sectionId,
      )
    ) {
      return {
        /*
         * Если пользователь prompt не менял, frontend не подменяет
         * серверный system prompt его локальной копией. Gateway сам
         * возьмёт актуальный section.system_prompt при создании snapshot.
         */
        promptOverrideEnabled: false,

        promptOverride: "",
      };
    }

    return {
      /*
       * Override передаётся только для реального несохранённого
       * пользовательского изменения рабочего prompt.
       */
      promptOverrideEnabled: true,

      promptOverride: (
        state.workingBySection.get(
          sectionId,
        )
        ?? ""
      ),
    };
  }


  setDisabled(
    true,
  );

  return {
    getOverride,

    setSection: async (
      sectionId,
    ) => {
      await loadSection(
        sectionId,
      );
    },
  };
}