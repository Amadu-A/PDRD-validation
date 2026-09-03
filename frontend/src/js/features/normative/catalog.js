// frontend/src/js/features/normative/catalog.js

/**
 * UI controller managed normative catalog.
 */

import {
  NORMATIVE_POLL_INTERVAL_MS,
} from "../../config.js";

import {
  requireElementWithin,
} from "../../dom.js";

import {
  createCategory,
  createSection,
  deleteCategory,
  deleteDocument,
  deleteSection,
  documentContentUrl,
  listCategories,
  listDocuments,
  listSections,
  moveDocument,
  queueDocument,
  updateCategory,
  updateSection,
  uploadDocument,
} from "./api.js";


const DOCUMENT_DRAG_TYPE = (
  "application/x-pdrd-normative-document"
);


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
      + "1.2 2h3.6l-.5-1h-2.6l-.5 1zM7 7l.86 12h8.28L17 7H7z"
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


function normalizePdfFiles(
  fileList,
) {
  return Array.from(
    fileList,
  ).filter(
    (file) => (
      file.type === "application/pdf"
      || file.name.toLowerCase().endsWith(
        ".pdf",
      )
    ),
  );
}


export function createNormativeCatalog(
  root,
) {
  const sectionSelect = requireElementWithin(
    root,
    "[data-normative-section-select]",
  );

  const createSectionButton = requireElementWithin(
    root,
    "[data-normative-section-create]",
  );

  const renameSectionButton = requireElementWithin(
    root,
    "[data-normative-section-rename]",
  );

  const deleteSectionButton = requireElementWithin(
    root,
    "[data-normative-section-delete]",
  );

  const createCategoryButton = requireElementWithin(
    root,
    "[data-normative-category-create]",
  );

  const selectAllButton = requireElementWithin(
    root,
    "[data-normative-select-all]",
  );

  const clearSelectionButton = requireElementWithin(
    root,
    "[data-normative-clear-all]",
  );

  const uploadZone = requireElementWithin(
    root,
    "[data-normative-upload-zone]",
  );

  const fileInput = requireElementWithin(
    root,
    "[data-normative-file-input]",
  );

  const tree = requireElementWithin(
    root,
    "[data-normative-tree]",
  );

  const statusElement = requireElementWithin(
    root,
    "[data-normative-status]",
  );


  const state = {
    sections: [],
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
          (item) => item.document_id,
        ),
    );

    for (const documentId of selected) {
      if (!available.has(documentId)) {
        selected.delete(
          documentId,
        );
      }
    }
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
          && result.has(category.parent_id)
          && !result.has(category.category_id)
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
        isReady(documentItem)
        && documentItem.category_id
        && categoryIds.has(
          documentItem.category_id,
        )
      ),
    );
  }


  function applyCategorySelection(
    categoryId,
    checked,
  ) {
    const selected = selectedSet();

    for (
      const documentItem
      of documentsInCategoryTree(categoryId)
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
      (item) => (
        selected.has(
          item.document_id,
        )
      ),
    ).length;

    return {
      checked: (
        selectedCount === readyDocuments.length
      ),
      indeterminate: (
        selectedCount > 0
        && selectedCount < readyDocuments.length
      ),
      disabled: false,
    };
  }


  function renderSectionSelect() {
    sectionSelect.replaceChildren();

    for (const section of state.sections) {
      const option = document.createElement(
        "option",
      );

      option.value = section.section_id;
      option.textContent = section.name;

      option.selected = (
        section.section_id === state.sectionId
      );

      sectionSelect.append(
        option,
      );
    }

    const disabled = !state.sectionId;

    sectionSelect.disabled = (
      state.sections.length === 0
    );

    renameSectionButton.disabled = disabled;
    deleteSectionButton.disabled = disabled;

    createCategoryButton.disabled = disabled;
    selectAllButton.disabled = disabled;
    clearSelectionButton.disabled = disabled;

    fileInput.disabled = disabled;

    uploadZone.dataset.disabled = (
      disabled
      ? "true"
      : "false"
    );
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

    row.dataset.documentId = (
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
      isReady(documentItem)
      && selectedSet().has(
        documentItem.document_id,
      )
    );

    checkbox.title = (
      checkbox.disabled
      ? "Документ станет доступен после индексации."
      : "Использовать документ при анализе."
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


    const link = document.createElement(
      "a",
    );

    link.className = (
      "normative-sidebar__document-link"
    );

    link.href = documentContentUrl(
      documentItem.document_id,
    );

    link.target = "_blank";
    link.rel = "noopener noreferrer";

    link.textContent = (
      documentItem.original_name
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
      link,
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
              await queueDocument(
                documentItem.document_id,
              );

              await refreshSectionData(
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
      isIndexing(documentItem)
      || documentItem.index_status === "deleting"
    );

    trashButton.addEventListener(
      "click",
      async () => {
        const confirmed = window.confirm(
          (
            "Удалить нормативный документ "
            + `"${documentItem.original_name}"?`
          ),
        );

        if (!confirmed) {
          return;
        }

        await runAction(
          async () => {
            await deleteDocument(
              documentItem.document_id,
            );

            selectedSet().delete(
              documentItem.document_id,
            );

            await refreshSectionData(
              false,
            );
          },
          "Документ удалён.",
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


  function createCategoryNode(
    category,
  ) {
    const wrapper = document.createElement(
      "div",
    );

    wrapper.className = (
      "normative-sidebar__category"
    );

    wrapper.dataset.categoryId = (
      category.category_id
    );


    const header = document.createElement(
      "div",
    );

    header.className = (
      "normative-sidebar__category-header"
    );


    const selection = document.createElement(
      "input",
    );

    selection.type = "checkbox";

    selection.className = (
      "normative-sidebar__checkbox"
    );

    const categoryDocuments = (
      documentsInCategoryTree(
        category.category_id,
      )
    );

    const categorySelection = (
      documentSelectionState(
        categoryDocuments,
      )
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
      "Выбрать READY-документы папки и подпапок."
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


    const createChildButton = (
      createTextButton(
        "+",
        "Создать вложенную папку",
      )
    );

    createChildButton.addEventListener(
      "click",
      async () => {
        await createCategoryInteractive(
          category.category_id,
        );
      },
    );


    const renameButton = createTextButton(
      "✎",
      "Переименовать папку",
    );

    renameButton.addEventListener(
      "click",
      async () => {
        const value = window.prompt(
          "Новое название папки:",
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
            await updateCategory(
              category.category_id,
              {
                name: value.trim(),
              },
            );

            await refreshSectionData(
              false,
            );
          },
          "Папка переименована.",
        );
      },
    );


    const deleteButton = createTrashButton(
      `Удалить папку ${category.name}`,
    );

    deleteButton.addEventListener(
      "click",
      async () => {
        const confirmed = window.confirm(
          (
            `Удалить папку "${category.name}"? `
            + "Документы останутся в разделе."
          ),
        );

        if (!confirmed) {
          return;
        }

        await runAction(
          async () => {
            await deleteCategory(
              category.category_id,
            );

            await refreshSectionData(
              false,
            );
          },
          "Папка удалена.",
        );
      },
    );


    actions.append(
      createChildButton,
      renameButton,
      deleteButton,
    );


    header.append(
      selection,
      name,
      actions,
    );


    header.addEventListener(
      "dragover",
      (event) => {
        event.preventDefault();

        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = (
            "move"
          );
        }
      },
    );

    header.addEventListener(
      "drop",
      async (event) => {
        event.preventDefault();

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


    const childCategories = (
      state.categories.filter(
        (candidate) => (
          candidate.parent_id
          === category.category_id
        ),
      )
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
        "Создайте или выберите нормативный раздел."
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

    rootHeader.textContent = "Без папки";

    rootHeader.addEventListener(
      "dragover",
      (event) => {
        event.preventDefault();
      },
    );

    rootHeader.addEventListener(
      "drop",
      async (event) => {
        event.preventDefault();

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
        "В разделе пока нет папок и документов."
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

    const requiresPolling = (
      state.documents.some(
        isIndexing,
      )
    );

    if (!requiresPolling) {
      return;
    }

    state.pollTimer = window.setTimeout(
      async () => {
        try {
          await refreshSectionData(
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


  async function refreshSectionData(
    silent = true,
  ) {
    if (!state.sectionId) {
      state.categories = [];
      state.documents = [];

      pruneSelection();
      renderTree();

      return;
    }

    const [
      categories,
      documents,
    ] = await Promise.all(
      [
        listCategories(
          state.sectionId,
        ),
        listDocuments(
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
        "Нормативный каталог обновлён.",
      );
    }
  }


  async function reloadSections(
    preferredSectionId = null,
  ) {
    state.sections = await listSections();

    const availableIds = new Set(
      state.sections.map(
        (section) => section.section_id,
      ),
    );

    if (
      preferredSectionId
      && availableIds.has(preferredSectionId)
    ) {
      state.sectionId = preferredSectionId;

    } else if (
      state.sectionId
      && availableIds.has(state.sectionId)
    ) {
      // Сохраняем текущий section.

    } else {
      state.sectionId = (
        state.sections[
          0
        ]?.section_id
        || null
      );
    }

    renderSectionSelect();

    await refreshSectionData(
      true,
    );
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
        "Сначала выберите нормативный раздел.",
        "error",
      );

      return;
    }

    const pdfFiles = normalizePdfFiles(
      files,
    );

    if (!pdfFiles.length) {
      setStatus(
        "Для нормативной базы поддерживаются PDF-файлы.",
        "error",
      );

      return;
    }

    const failures = [];

    for (const file of pdfFiles) {
      try {
        setStatus(
          `Загружаем ${file.name}…`,
        );

        const documentItem = await uploadDocument(
          state.sectionId,
          {
            file,
            categoryId,
          },
        );

        try {
          await queueDocument(
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

    await refreshSectionData(
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
      "PDF загружен и поставлен в очередь индексации.",
    );
  }


  async function handleDrop(
    event,
    categoryId,
  ) {
    const files = normalizePdfFiles(
      event.dataTransfer?.files || [],
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
        await moveDocument(
          documentId,
          categoryId,
        );

        await refreshSectionData(
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
        : "Название папки:",
    );

    if (
      name === null
      || !name.trim()
    ) {
      return;
    }

    await runAction(
      async () => {
        await createCategory(
          state.sectionId,
          {
            name: name.trim(),
            parentId,
          },
        );

        await refreshSectionData(
          false,
        );
      },
      "Папка создана.",
    );
  }


  function bindSectionControls() {
    sectionSelect.addEventListener(
      "change",
      async () => {
        state.sectionId = (
          sectionSelect.value
          || null
        );

        await runAction(
          async () => {
            await refreshSectionData(
              true,
            );
          },
          "Раздел загружен.",
        );
      },
    );


    createSectionButton.addEventListener(
      "click",
      async () => {
        const name = window.prompt(
          "Название нового нормативного раздела:",
        );

        if (
          name === null
          || !name.trim()
        ) {
          return;
        }

        await runAction(
          async () => {
            const section = await createSection(
              name.trim(),
            );

            await reloadSections(
              section.section_id,
            );
          },
          "Раздел создан.",
        );
      },
    );


    renameSectionButton.addEventListener(
      "click",
      async () => {
        const section = state.sections.find(
          (item) => (
            item.section_id
            === state.sectionId
          ),
        );

        if (!section) {
          return;
        }

        const name = window.prompt(
          "Новое название раздела:",
          section.name,
        );

        if (
          name === null
          || !name.trim()
          || name.trim() === section.name
        ) {
          return;
        }

        await runAction(
          async () => {
            await updateSection(
              section.section_id,
              {
                name: name.trim(),
              },
            );

            await reloadSections(
              section.section_id,
            );
          },
          "Раздел переименован.",
        );
      },
    );


    deleteSectionButton.addEventListener(
      "click",
      async () => {
        const section = state.sections.find(
          (item) => (
            item.section_id
            === state.sectionId
          ),
        );

        if (!section) {
          return;
        }

        const confirmed = window.confirm(
          (
            `Удалить раздел "${section.name}"? `
            + "Раздел должен быть пустым."
          ),
        );

        if (!confirmed) {
          return;
        }

        await runAction(
          async () => {
            await deleteSection(
              section.section_id,
            );

            state.selectedBySection.delete(
              section.section_id,
            );

            state.sectionId = null;

            await reloadSections();
          },
          "Раздел удалён.",
        );
      },
    );
  }


  function bindSelectionControls() {
    selectAllButton.addEventListener(
      "click",
      () => {
        const selected = selectedSet();

        for (
          const documentItem
          of state.documents
        ) {
          if (isReady(documentItem)) {
            selected.add(
              documentItem.document_id,
            );
          }
        }

        renderTree();

        setStatus(
          "Выбраны все готовые документы раздела.",
        );
      },
    );


    clearSelectionButton.addEventListener(
      "click",
      () => {
        selectedSet().clear();

        renderTree();

        setStatus(
          "Выбор документов очищен.",
        );
      },
    );


    createCategoryButton.addEventListener(
      "click",
      async () => {
        await createCategoryInteractive(
          null,
        );
      },
    );
  }


  function bindUploadControls() {
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


  async function start() {
    bindSectionControls();
    bindSelectionControls();
    bindUploadControls();

    try {
      setStatus(
        "Загружаем нормативный каталог…",
      );

      await reloadSections();

      setStatus(
        state.sectionId
          ? "Нормативный каталог готов."
          : "Нормативные разделы пока не созданы.",
      );

    } catch (error) {
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
            isReady(documentItem)
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
    start,
  };
}