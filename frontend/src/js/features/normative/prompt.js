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


  const state = {
    sectionId: null,

    workingBySection: new Map(),

    systemBySection: new Map(),

    requestToken: 0,
  };


  function setStatus(
    message,
    stateName = "normal",
  ) {
    statusElement.textContent = message;

    statusElement.dataset.state = stateName;
  }


  function setDisabled(
    disabled,
  ) {
    textarea.disabled = disabled;

    saveButton.disabled = disabled;

    restoreButton.disabled = disabled;
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


  function renderDirtyState() {
    if (!state.sectionId) {
      setStatus(
        "Выберите нормативный раздел.",
      );

      return;
    }

    if (
      currentWorkingPrompt()
      === currentSystemPrompt()
    ) {
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

    if (!sectionId) {
      textarea.value = "";

      setDisabled(
        true,
      );

      setStatus(
        "Выберите нормативный раздел.",
      );

      return;
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
        return;
      }

      state.systemBySection.set(
        sectionId,
        section.system_prompt,
      );

      if (
        replaceWorking
        || !state.workingBySection.has(
          sectionId,
        )
      ) {
        state.workingBySection.set(
          sectionId,
          section.system_prompt,
        );
      }

      textarea.value = (
        state.workingBySection.get(
          sectionId,
        )
        ?? ""
      );

      setDisabled(
        false,
      );

      renderDirtyState();

    } catch (error) {
      if (
        token
        !== state.requestToken
      ) {
        return;
      }

      textarea.value = "";

      setDisabled(
        true,
      );

      setStatus(
        error instanceof Error
          ? error.message
          : String(error),
        "error",
      );
    }
  }


  async function saveSystemPrompt() {
    if (!state.sectionId) {
      return;
    }

    const sectionId = state.sectionId;

    const workingPrompt = textarea.value;

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

      textarea.value = (
        section.system_prompt
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

    await loadSection(
      sectionId,
      {
        replaceWorking: true,
      },
    );

    if (
      state.sectionId
      === sectionId
    ) {
      setStatus(
        "Рабочий prompt восстановлен из системного.",
      );
    }
  }


  textarea.addEventListener(
    "input",
    () => {
      if (!state.sectionId) {
        return;
      }

      state.workingBySection.set(
        state.sectionId,
        textarea.value,
      );

      renderDirtyState();
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


  function getOverride(
    sectionId,
  ) {
    if (
      !sectionId
      || sectionId !== state.sectionId
      || !state.workingBySection.has(
        sectionId,
      )
    ) {
      return {
        promptOverrideEnabled: false,
        promptOverride: "",
      };
    }

    return {
      /*
       * Всегда передаём рабочий текст как snapshot override.
       *
       * Это гарантирует, что между нажатием "Запустить анализ"
       * и чтением DB другим процессом prompt не изменится.
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