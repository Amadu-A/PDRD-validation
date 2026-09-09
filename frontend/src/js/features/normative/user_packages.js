// frontend/src/js/features/normative/user_packages.js

/**
 * UI controller пользовательских пакетов managed catalog.
 */

import {
  NORMATIVE_POLL_INTERVAL_MS,
} from "../../config.js";

import {
  requireElementWithin,
} from "../../dom.js";

import {
  createUserPackageCategory,
  deleteUserPackageCategory,
  deleteUserPackageDocument,
  listUserPackageCategories,
  listUserPackageDocuments,
  moveUserPackageDocument,
  queueUserPackageDocument,
  updateUserPackageCategory,
  uploadUserPackageDocument,
  userPackageDocumentContentUrl,
} from "./api.js";


const DOCUMENT_DRAG_TYPE = (
  "application/x-pdrd-user-package-document"
);

const SUPPORTED_FILE_ACCEPT = (
  ".pdf,.doc,.docx,"
  + "application/pdf,"
  + "application/msword,"
  + "application/vnd.openxmlformats-officedocument."
  + "wordprocessingml.document"
);

const SUPPORTED_EXTENSIONS = [
  ".pdf",
  ".doc",
  ".docx",
];

const STATUS_LABELS = {
  uploaded: "Загружен",
  queued: "В очереди",
  indexing: "Индексируется",
  ready: "Готов",
  failed: "Ошибка",
  deleting: "Удаляется",
};


function createTextButton(
  text,
  title,
) {
  const button = document.createElement(
    "button",
  );

  button.type = "button";

  button.className = (
    "normative-sidebar__icon-button"
  );

  button.textContent = text;

  button.title = title;

  button.setAttribute(
    "aria-label",
    title,
  );

  return button;
}


function createTrashIcon() {
  const namespace = (
    "http://www.w3.org/2000/svg"
  );

  const svg = document.createElementNS(
    namespace,
    "svg",
  );

  svg.setAttribute(
    "viewBox",
    "0 0 24 24",
  );

  svg.setAttribute(
    "width",
    "17",
  );

  svg.setAttribute(
    "height",
    "17",
  );

  svg.setAttribute(
    "aria-hidden",
    "true",
  );

  const path = document.createElementNS(
    namespace,
    "path",
  );

  path.setAttribute(
    "d",
    (
      "M9 3h6l1 2h4v2h-1l-1 14H6L5 7H4V5h4l1-2zm"
      + "1.2 2h3.6l-.5-1h-2.6l-.5 1z"
      + "M7 7l.86 12h8.28L17 7H7z"
    ),
  );

  svg.append(
    path,
  );

  return svg;
}


function createTrashButton(
  title,
) {
  const button = document.createElement(
    "button",
  );

  button.type = "button";

  button.className = (
    "normative-sidebar__trash-button"
  );

  button.title = title;

  button.setAttribute(
    "aria-label",
    title,
  );

  button.append(
    createTrashIcon(),
  );

  return button;
}


function isReady(
  documentItem,
) {
  return (
    documentItem.index_status === "ready"
    && documentItem.ready_for_analysis === true
  );
}


function isIndexing(
  documentItem,
) {
  return (
    documentItem.index_status === "queued"
    || documentItem.index_status === "indexing"
  );
}


function isSupportedFile(
  file,
) {
  const lowerName = file.name.toLowerCase();

  return SUPPORTED_EXTENSIONS.some(
    (extension) => (
      lowerName.endsWith(
        extension,
      )
    ),
  );
}


function normalizeFiles(
  fileList,
) {
  return Array.from(
    fileList || [],
  ).filter(
    isSupportedFile,
  );
}


export function createUserPackageCatalog(
  root,
) {
  const createPackageButton = requireElementWithin(
    root,
    "[data-user-package-create]",
  );

  const selectAllButton = requireElementWithin(
    root,
    "[data-user-packages-select-all]",
  );

  const clearSelectionButton = requireElementWithin(
    root,
    "[data-user-packages-clear]",
  );

  const uploadZone = requireElementWithin(
    root,
    "[data-user-package-upload-zone]",
  );

  const fileInput = requireElementWithin(
    root,
    "[data-user-package-file-input]",
  );

  const tree = requireElementWithin(
    root,
    "[data-user-packages-tree]",
  );

  const statusElement = requireElementWithin(
    root,
    "[data-user-packages-status]",
  );


  const state = {
    sectionId: null,

    categories: [],

    documents: [],

    selectedBySection: new Map(),

    pollTimer: null,
  };


  function setStatus(
    message,
    stateName = "normal",
  ) {
    statusElement.textContent = message;

    statusElement.dataset.state = stateName;
  }


  function selectedSet() {
    if (!state.sectionId) {
      return new Set();
    }

    if (
      !state.selectedBySection.has(
        state.sectionId,
      )
    ) {
      state.selectedBySection.set(
        state.sectionId,
        new Set(),
      );
    }

    return state.selectedBySection.get(
      state.sectionId,
    );
  }


  function pruneSelection() {
    const selected = selectedSet();

    const available = new Set(
      state.documents
        .filter(
          isReady,
        )
        .map(
          (documentItem) => (
            documentItem.document_id
          ),
        ),
    );

    for (const documentId of selected) {
      if (
        !available.has(
          documentId,
        )
      ) {
        selected.delete(
          documentId,
        );
      }
    }
  }


  function syncControls() {
    const disabled = !state.sectionId;

    createPackageButton.disabled = disabled;

    selectAllButton.disabled = disabled;

    clearSelectionButton.disabled = disabled;

    fileInput.disabled = disabled;

    uploadZone.dataset.disabled = (
      disabled
        ? "true"
        : "false"
    );

    uploadZone.tabIndex = (
      disabled
        ? -1
        : 0
    );
  }


  function childCategoryIds(
    categoryId,
  ) {
    const result = new Set(
      [
        categoryId,
      ],
    );

    let changed = true;

    while (changed) {
      changed = false;

      for (const category of state.categories) {
        if (
          category.parent_id
          && result.has(
            category.parent_id,
          )
          && !result.has(
            category.category_id,
          )
        ) {
          result.add(
            category.category_id,
          );

          changed = true;
        }
      }
    }

    return result;
  }


  function documentsInCategoryTree(
    categoryId,
  ) {
    const categoryIds = childCategoryIds(
      categoryId,
    );

    return state.documents.filter(
      (documentItem) => (
        isReady(
          documentItem,
        )
        && documentItem.category_id
        && categoryIds.has(
          documentItem.category_id,
        )
      ),
    );
  }


  function documentSelectionState(
    documentItems,
  ) {
    const readyDocuments = documentItems.filter(
      isReady,
    );

    if (!readyDocuments.length) {
      return {
        checked: false,
        indeterminate: false,
        disabled: true,
      };
    }

    const selected = selectedSet();

    const selectedCount = readyDocuments.filter(
      (documentItem) => (
        selected.has(
          documentItem.document_id,
        )
      ),
    ).length;

    return {
      checked: (
        selectedCount
        === readyDocuments.length
      ),

      indeterminate: (
        selectedCount > 0
        && selectedCount < readyDocuments.length
      ),

      disabled: false,
    };
  }


  function applyCategorySelection(
    categoryId,
    checked,
  ) {
    const selected = selectedSet();

    for (
      const documentItem
      of documentsInCategoryTree(
        categoryId,
      )
    ) {
      if (checked) {
        selected.add(
          documentItem.document_id,
        );

      } else {
        selected.delete(
          documentItem.document_id,
        );
      }
    }

    renderTree();
  }


  function createDocumentName(
    documentItem,
  ) {
    const canOpen = (
      documentItem.mime_type
      === "application/pdf"
      || isReady(
        documentItem,
      )
    );

    if (canOpen) {
      const link = document.createElement(
        "a",
      );

      link.className = (
        "normative-sidebar__document-link"
      );

      link.href = userPackageDocumentContentUrl(
        documentItem.document_id,
      );

      link.target = "_blank";

      link.rel = "noopener noreferrer";

      link.textContent = (
        documentItem.original_name
      );

      return link;
    }

    const text = document.createElement(
      "span",
    );

    text.className = (
      "normative-sidebar__document-link "
      + "normative-sidebar__document-link--disabled"
    );

    text.textContent = (
      documentItem.original_name
    );

    text.title = (
      "Word preview станет доступен "
      + "после завершения индексации."
    );

    return text;
  }


  function createDocumentRow(
    documentItem,
  ) {
    const row = document.createElement(
      "div",
    );

    row.className = (
      "normative-sidebar__document"
    );

    row.draggable = true;

    row.dataset.userPackageDocumentId = (
      documentItem.document_id
    );


    const checkbox = document.createElement(
      "input",
    );

    checkbox.type = "checkbox";

    checkbox.className = (
      "normative-sidebar__checkbox"
    );

    checkbox.disabled = !isReady(
      documentItem,
    );

    checkbox.checked = (
      isReady(
        documentItem,
      )
      && selectedSet().has(
        documentItem.document_id,
      )
    );

    checkbox.title = (
      checkbox.disabled
        ? "Документ станет доступен после индексации."
        : "Использовать пользовательский документ при анализе."
    );

    checkbox.addEventListener(
      "change",
      () => {
        const selected = selectedSet();

        if (checkbox.checked) {
          selected.add(
            documentItem.document_id,
          );

        } else {
          selected.delete(
            documentItem.document_id,
          );
        }

        renderTree();
      },
    );


    const content = document.createElement(
      "div",
    );

    content.className = (
      "normative-sidebar__document-content"
    );

    const documentName = createDocumentName(
      documentItem,
    );


    const meta = document.createElement(
      "div",
    );

    meta.className = (
      "normative-sidebar__document-meta"
    );


    const status = document.createElement(
      "span",
    );

    status.className = (
      "normative-sidebar__status-badge"
    );

    status.dataset.status = (
      documentItem.index_status
    );

    status.textContent = (
      STATUS_LABELS[
        documentItem.index_status
      ]
      || documentItem.index_status
    );

    meta.append(
      status,
    );


    if (documentItem.index_error) {
      const errorText = document.createElement(
        "span",
      );

      errorText.className = (
        "normative-sidebar__document-error"
      );

      errorText.textContent = (
        documentItem.index_error
      );

      errorText.title = (
        documentItem.index_error
      );

      meta.append(
        errorText,
      );
    }


    content.append(
      documentName,
      meta,
    );


    const actions = document.createElement(
      "div",
    );

    actions.className = (
      "normative-sidebar__document-actions"
    );


    if (
      documentItem.index_status === "uploaded"
      || documentItem.index_status === "failed"
    ) {
      const retryButton = createTextButton(
        "↻",
        "Запустить индексацию",
      );

      retryButton.addEventListener(
        "click",
        async () => {
          await runAction(
            async () => {
              await queueUserPackageDocument(
                documentItem.document_id,
              );

              await refresh(
                false,
              );
            },
            "Документ поставлен в очередь.",
          );
        },
      );

      actions.append(
        retryButton,
      );
    }


    const trashButton = createTrashButton(
      `Удалить ${documentItem.original_name}`,
    );

    trashButton.disabled = (
      isIndexing(
        documentItem,
      )
      || documentItem.index_status === "deleting"
    );

    trashButton.addEventListener(
      "click",
      async () => {
        const confirmed = window.confirm(
          (
            "Удалить пользовательский документ "
            + `"${documentItem.original_name}"?`
          ),
        );

        if (!confirmed) {
          return;
        }

        await runAction(
          async () => {
            await deleteUserPackageDocument(
              documentItem.document_id,
            );

            selectedSet().delete(
              documentItem.document_id,
            );

            await refresh(
              false,
            );
          },
          "Пользовательский документ удалён.",
        );
      },
    );

    actions.append(
      trashButton,
    );


    row.addEventListener(
      "dragstart",
      (event) => {
        event.dataTransfer?.setData(
          DOCUMENT_DRAG_TYPE,
          documentItem.document_id,
        );

        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = (
            "move"
          );
        }
      },
    );


    row.append(
      checkbox,
      content,
      actions,
    );

    return row;
  }


  function openCategoryFilePicker(
    categoryId,
  ) {
    const picker = document.createElement(
      "input",
    );

    picker.type = "file";

    picker.multiple = true;

    picker.accept = SUPPORTED_FILE_ACCEPT;

    picker.className = (
      "normative-sidebar__file-input"
    );

    picker.addEventListener(
      "change",
      async () => {
        await uploadFiles(
          picker.files,
          categoryId,
        );

        picker.remove();
      },
      {
        once: true,
      },
    );

    root.append(
      picker,
    );

    picker.click();
  }


  function createCategoryNode(
    category,
  ) {
    const wrapper = document.createElement(
      "div",
    );

    wrapper.className = (
      "normative-sidebar__category"
    );

    wrapper.dataset.userPackageCategoryId = (
      category.category_id
    );


    const header = document.createElement(
      "div",
    );

    header.className = (
      "normative-sidebar__category-header"
    );

    header.dataset.dragOver = "false";


    const selection = document.createElement(
      "input",
    );

    selection.type = "checkbox";

    selection.className = (
      "normative-sidebar__checkbox"
    );

    const categorySelection = documentSelectionState(
      documentsInCategoryTree(
        category.category_id,
      ),
    );

    selection.checked = (
      categorySelection.checked
    );

    selection.indeterminate = (
      categorySelection.indeterminate
    );

    selection.disabled = (
      categorySelection.disabled
    );

    selection.title = (
      "Выбрать READY-документы пакета и вложенных папок."
    );

    selection.addEventListener(
      "change",
      () => {
        applyCategorySelection(
          category.category_id,
          selection.checked,
        );
      },
    );


    const name = document.createElement(
      "span",
    );

    name.className = (
      "normative-sidebar__category-name"
    );

    name.textContent = category.name;


    const actions = document.createElement(
      "div",
    );

    actions.className = (
      "normative-sidebar__category-actions"
    );


    const uploadButton = createTextButton(
      "⇧",
      "Добавить PDF/DOC/DOCX в этот пакет",
    );

    uploadButton.addEventListener(
      "click",
      () => {
        openCategoryFilePicker(
          category.category_id,
        );
      },
    );


    const childButton = createTextButton(
      "+",
      "Создать вложенную папку",
    );

    childButton.addEventListener(
      "click",
      async () => {
        await createCategoryInteractive(
          category.category_id,
        );
      },
    );


    const renameButton = createTextButton(
      "✎",
      "Переименовать пакет или папку",
    );

    renameButton.addEventListener(
      "click",
      async () => {
        const value = window.prompt(
          "Новое название:",
          category.name,
        );

        if (
          value === null
          || !value.trim()
          || value.trim() === category.name
        ) {
          return;
        }

        await runAction(
          async () => {
            await updateUserPackageCategory(
              category.category_id,
              {
                name: value.trim(),
              },
            );

            await refresh(
              false,
            );
          },
          "Название изменено.",
        );
      },
    );


    const deleteButton = createTrashButton(
      `Удалить ${category.name}`,
    );

    deleteButton.addEventListener(
      "click",
      async () => {
        const confirmed = window.confirm(
          (
            `Удалить "${category.name}"? `
            + "Документы останутся в разделе без пакета."
          ),
        );

        if (!confirmed) {
          return;
        }

        await runAction(
          async () => {
            await deleteUserPackageCategory(
              category.category_id,
            );

            await refresh(
              false,
            );
          },
          "Пакет или папка удалены.",
        );
      },
    );


    actions.append(
      uploadButton,
      childButton,
      renameButton,
      deleteButton,
    );


    header.append(
      selection,
      name,
      actions,
    );


    header.addEventListener(
      "dragenter",
      (event) => {
        event.preventDefault();

        header.dataset.dragOver = "true";
      },
    );


    header.addEventListener(
      "dragover",
      (event) => {
        event.preventDefault();

        header.dataset.dragOver = "true";

        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = (
            event.dataTransfer.files.length
              ? "copy"
              : "move"
          );
        }
      },
    );


    header.addEventListener(
      "dragleave",
      (event) => {
        if (
          event.relatedTarget
          && header.contains(
            event.relatedTarget,
          )
        ) {
          return;
        }

        header.dataset.dragOver = "false";
      },
    );


    header.addEventListener(
      "drop",
      async (event) => {
        event.preventDefault();

        header.dataset.dragOver = "false";

        await handleDrop(
          event,
          category.category_id,
        );
      },
    );


    const children = document.createElement(
      "div",
    );

    children.className = (
      "normative-sidebar__category-children"
    );


    const directDocuments = state.documents.filter(
      (documentItem) => (
        documentItem.category_id
        === category.category_id
      ),
    );

    for (
      const documentItem
      of directDocuments
    ) {
      children.append(
        createDocumentRow(
          documentItem,
        ),
      );
    }


    const childCategories = state.categories.filter(
      (candidate) => (
        candidate.parent_id
        === category.category_id
      ),
    );

    for (const child of childCategories) {
      children.append(
        createCategoryNode(
          child,
        ),
      );
    }


    wrapper.append(
      header,
      children,
    );

    return wrapper;
  }


  function renderTree() {
    tree.replaceChildren();

    if (!state.sectionId) {
      const empty = document.createElement(
        "p",
      );

      empty.className = (
        "normative-sidebar__empty"
      );

      empty.textContent = (
        "Выберите нормативный раздел."
      );

      tree.append(
        empty,
      );

      return;
    }


    const rootHeader = document.createElement(
      "div",
    );

    rootHeader.className = (
      "normative-sidebar__root-header"
    );

    rootHeader.textContent = "Без пакета";

    rootHeader.dataset.dragOver = "false";


    rootHeader.addEventListener(
      "dragenter",
      (event) => {
        event.preventDefault();

        rootHeader.dataset.dragOver = "true";
      },
    );


    rootHeader.addEventListener(
      "dragover",
      (event) => {
        event.preventDefault();

        rootHeader.dataset.dragOver = "true";
      },
    );


    rootHeader.addEventListener(
      "dragleave",
      () => {
        rootHeader.dataset.dragOver = "false";
      },
    );


    rootHeader.addEventListener(
      "drop",
      async (event) => {
        event.preventDefault();

        rootHeader.dataset.dragOver = "false";

        await handleDrop(
          event,
          null,
        );
      },
    );


    tree.append(
      rootHeader,
    );


    const rootDocuments = state.documents.filter(
      (documentItem) => (
        documentItem.category_id === null
      ),
    );

    for (
      const documentItem
      of rootDocuments
    ) {
      tree.append(
        createDocumentRow(
          documentItem,
        ),
      );
    }


    const rootCategories = state.categories.filter(
      (category) => (
        category.parent_id === null
      ),
    );

    for (const category of rootCategories) {
      tree.append(
        createCategoryNode(
          category,
        ),
      );
    }


    if (
      state.documents.length === 0
      && state.categories.length === 0
    ) {
      const empty = document.createElement(
        "p",
      );

      empty.className = (
        "normative-sidebar__empty"
      );

      empty.textContent = (
        "Пользовательские пакеты пока не созданы."
      );

      tree.append(
        empty,
      );
    }
  }


  function clearPolling() {
    if (state.pollTimer !== null) {
      window.clearTimeout(
        state.pollTimer,
      );

      state.pollTimer = null;
    }
  }


  function schedulePolling() {
    clearPolling();

    if (
      !state.documents.some(
        isIndexing,
      )
    ) {
      return;
    }

    state.pollTimer = window.setTimeout(
      async () => {
        try {
          await refresh(
            true,
          );

        } catch (error) {
          setStatus(
            error instanceof Error
              ? error.message
              : String(error),
            "error",
          );
        }
      },
      NORMATIVE_POLL_INTERVAL_MS,
    );
  }


  async function refresh(
    silent = true,
  ) {
    if (!state.sectionId) {
      state.categories = [];

      state.documents = [];

      renderTree();

      return;
    }

    const [
      categories,
      documents,
    ] = await Promise.all(
      [
        listUserPackageCategories(
          state.sectionId,
        ),

        listUserPackageDocuments(
          state.sectionId,
        ),
      ],
    );

    state.categories = categories;

    state.documents = documents;

    pruneSelection();

    renderTree();

    schedulePolling();

    if (!silent) {
      setStatus(
        "Пользовательские пакеты обновлены.",
      );
    }
  }


  async function runAction(
    action,
    successMessage,
  ) {
    try {
      setStatus(
        "Выполняется…",
      );

      await action();

      if (successMessage) {
        setStatus(
          successMessage,
        );
      }

    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : String(error),
        "error",
      );
    }
  }


  async function uploadFiles(
    files,
    categoryId = null,
  ) {
    if (!state.sectionId) {
      setStatus(
        "Сначала выберите раздел.",
        "error",
      );

      return;
    }

    const packageFiles = normalizeFiles(
      files,
    );

    if (!packageFiles.length) {
      setStatus(
        "Поддерживаются PDF, DOC и DOCX.",
        "error",
      );

      return;
    }

    const failures = [];

    for (const file of packageFiles) {
      try {
        setStatus(
          `Загружаем ${file.name}…`,
        );

        const documentItem = (
          await uploadUserPackageDocument(
            state.sectionId,
            {
              file,
              categoryId,
            },
          )
        );

        try {
          await queueUserPackageDocument(
            documentItem.document_id,
          );

        } catch (error) {
          failures.push(
            `${file.name}: ${
              error instanceof Error
                ? error.message
                : String(error)
            }`,
          );
        }

      } catch (error) {
        failures.push(
          `${file.name}: ${
            error instanceof Error
              ? error.message
              : String(error)
          }`,
        );
      }
    }

    await refresh(
      true,
    );

    if (failures.length) {
      setStatus(
        failures.join(
          " | ",
        ),
        "error",
      );

      return;
    }

    setStatus(
      "Документы загружены и поставлены "
      + "в очередь индексации.",
    );
  }


  async function handleDrop(
    event,
    categoryId,
  ) {
    const files = normalizeFiles(
      event.dataTransfer?.files
      || [],
    );

    if (files.length) {
      await uploadFiles(
        files,
        categoryId,
      );

      return;
    }

    const documentId = (
      event.dataTransfer?.getData(
        DOCUMENT_DRAG_TYPE,
      )
      || ""
    );

    if (!documentId) {
      return;
    }

    await runAction(
      async () => {
        await moveUserPackageDocument(
          documentId,
          categoryId,
        );

        await refresh(
          false,
        );
      },
      "Документ перемещён.",
    );
  }


  async function createCategoryInteractive(
    parentId = null,
  ) {
    if (!state.sectionId) {
      return;
    }

    const name = window.prompt(
      parentId
        ? "Название вложенной папки:"
        : "Название пользовательского пакета:",
    );

    if (
      name === null
      || !name.trim()
    ) {
      return;
    }

    await runAction(
      async () => {
        await createUserPackageCategory(
          state.sectionId,
          {
            name: name.trim(),
            parentId,
          },
        );

        await refresh(
          false,
        );
      },
      (
        parentId
          ? "Папка создана."
          : "Пользовательский пакет создан."
      ),
    );
  }


  function bindControls() {
    createPackageButton.addEventListener(
      "click",
      async () => {
        await createCategoryInteractive(
          null,
        );
      },
    );


    selectAllButton.addEventListener(
      "click",
      () => {
        const selected = selectedSet();

        for (
          const documentItem
          of state.documents
        ) {
          if (
            isReady(
              documentItem,
            )
          ) {
            selected.add(
              documentItem.document_id,
            );
          }
        }

        renderTree();

        setStatus(
          "Выбраны все готовые пользовательские документы.",
        );
      },
    );


    clearSelectionButton.addEventListener(
      "click",
      () => {
        selectedSet().clear();

        renderTree();

        setStatus(
          "Выбор пользовательских документов очищен.",
        );
      },
    );


    fileInput.addEventListener(
      "change",
      async () => {
        await uploadFiles(
          fileInput.files,
          null,
        );

        fileInput.value = "";
      },
    );


    uploadZone.addEventListener(
      "click",
      () => {
        if (!fileInput.disabled) {
          fileInput.click();
        }
      },
    );


    uploadZone.addEventListener(
      "keydown",
      (event) => {
        if (
          event.key === "Enter"
          || event.key === " "
        ) {
          event.preventDefault();

          if (!fileInput.disabled) {
            fileInput.click();
          }
        }
      },
    );


    uploadZone.addEventListener(
      "dragover",
      (event) => {
        event.preventDefault();

        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = (
            "copy"
          );
        }
      },
    );


    uploadZone.addEventListener(
      "drop",
      async (event) => {
        event.preventDefault();

        await handleDrop(
          event,
          null,
        );
      },
    );
  }


  function start() {
    bindControls();

    syncControls();

    renderTree();

    setStatus(
      "Выберите нормативный раздел.",
    );
  }


  async function setSection(
    sectionId,
  ) {
    clearPolling();

    state.sectionId = (
      sectionId
      || null
    );

    state.categories = [];

    state.documents = [];

    syncControls();

    renderTree();

    if (!state.sectionId) {
      setStatus(
        "Выберите нормативный раздел.",
      );

      return;
    }

    try {
      setStatus(
        "Загружаем пользовательские пакеты…",
      );

      await refresh(
        true,
      );

      setStatus(
        "Пользовательские пакеты готовы.",
      );

    } catch (error) {
      state.categories = [];

      state.documents = [];

      renderTree();

      setStatus(
        error instanceof Error
          ? error.message
          : String(error),
        "error",
      );
    }
  }


  function getSelection() {
    if (!state.sectionId) {
      return null;
    }

    const selected = selectedSet();

    return {
      sectionId: state.sectionId,

      documentIds: state.documents
        .filter(
          (documentItem) => (
            isReady(
              documentItem,
            )
            && selected.has(
              documentItem.document_id,
            )
          ),
        )
        .map(
          (documentItem) => (
            documentItem.document_id
          ),
        ),
    };
  }


  return {
    getSelection,
    setSection,
    start,
  };
}