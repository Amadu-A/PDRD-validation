// frontend/src/js/app.js

/**
 * Composition root браузерного приложения PDRD Validation.
 */

import {
  createModal,
} from "./components/modal.js";

import {
  createResultView,
} from "./components/result.js";

import {
  requireElement,
} from "./dom.js";

import {
  createAnalysisController,
} from "./features/analysis/controller.js";

import {
  createAnalysisForm,
} from "./features/analysis/form.js";

import {
  createNormativeCatalog,
} from "./features/normative/catalog.js";

import {
  createNormativePromptEditor,
} from "./features/normative/prompt.js";


const analysisFormElement = requireElement(
  "[data-analysis-form]",
);

const submitButton = requireElement(
  "[data-submit-button]",
);

const normativeRoot = requireElement(
  "[data-normative-sidebar]",
);


const promptEditor = createNormativePromptEditor(
  normativeRoot,
);


const normativeCatalog = createNormativeCatalog(
  normativeRoot,
  {
    onSectionChange: async (
      sectionId,
    ) => {
      await promptEditor.setSection(
        sectionId,
      );
    },
  },
);


const modal = createModal({
  modalElement: requireElement(
    "[data-analysis-modal]",
  ),

  textElement: requireElement(
    "[data-analysis-modal-text]",
  ),

  submitButton,

  jobElement: requireElement(
    "[data-analysis-modal-job]",
  ),

  jobIdElement: requireElement(
    "[data-analysis-modal-job-id]",
  ),

  copyButton: requireElement(
    "[data-analysis-modal-copy]",
  ),

  copyStatusElement: requireElement(
    "[data-analysis-modal-copy-status]",
  ),
});


const resultView = createResultView(
  requireElement(
    "[data-analysis-result]",
  ),
);


function getNormativeSelection() {
  const selection = (
    normativeCatalog.getSelection()
  );

  if (!selection) {
    return null;
  }

  const prompt = promptEditor.getOverride(
    selection.sectionId,
  );

  return {
    ...selection,
    ...prompt,
  };
}


const analysisForm = createAnalysisForm({
  pdfInput: requireElement(
    "[data-pdf-input]",
  ),

  cadInput: requireElement(
    "[data-cad-input]",
  ),

  pagesInput: requireElement(
    "[data-pages-input]",
  ),

  pagesHint: requireElement(
    "[data-pages-hint]",
  ),

  useExplanatoryNoteInput: requireElement(
    "[data-explanatory-note-input]",
  ),

  noteStartPageInput: requireElement(
    "[data-note-start-input]",
  ),

  noteEndPageInput: requireElement(
    "[data-note-end-input]",
  ),

  getNormativeSelection,
});


const analysisController = createAnalysisController({
  analysisForm,
  modal,
  resultView,
});


analysisForm.bind();

analysisFormElement.addEventListener(
  "submit",
  analysisController.submit,
);

void normativeCatalog.start();