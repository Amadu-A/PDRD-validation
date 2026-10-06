// frontend/src/js/app.js

/**
 * Точка сборки браузерного приложения PDRD Validation.
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
  mountExperienceNavigation,
} from "./features/experience/navigation.js";

import {
  createNormativeCatalog,
} from "./features/normative/catalog.js";

import { createUserPackageCatalog } from "./features/normative/user_packages.js";

import {
  createNormativePromptEditor,
} from "./features/normative/prompt.js";

import {
  createReviewPersistence,
} from "./features/review/persistence.js";
import { bindReportRestoration } from "./features/analysis/restore.js";
import { mountAnalysisHistoryNavigation } from "./features/analysis/history.js";
import { bindMainIdentity } from "./features/auth/main-page.js";
import { currentSession } from "./features/auth/session.js";
import { hasPermission } from "./features/auth/access.js";
import { mountAuthorizedReview, reviewCapabilities } from "./features/review/access.js";

import {
  createTechnicalAssignmentFilePicker,
} from "./features/technical_assignment/file.js";


const analysisFormElement = requireElement(
  "[data-analysis-form]",
);

const submitButton = requireElement(
  "[data-submit-button]",
);

const normativeRoot = requireElement(
  "[data-normative-sidebar]",
);


mountExperienceNavigation(requireElement(".page__content"));
const analysisHistory = mountAnalysisHistoryNavigation(requireElement(".page__content"));


const technicalAssignmentFilePicker = (
  createTechnicalAssignmentFilePicker(
    normativeRoot,
    {
      submitButton,
    },
  )
);

technicalAssignmentFilePicker.bind();


const promptEditor = createNormativePromptEditor(
  normativeRoot,
);


const userPackageCatalog = createUserPackageCatalog(normativeRoot);
userPackageCatalog.start();

const normativeCatalog = createNormativeCatalog(
  normativeRoot,
  {
    onSectionChange: async (
      sectionId,
    ) => {
      const session = currentSession();
      await Promise.all([
        (hasPermission(session, "working_prompt.use") || hasPermission(session, "system_prompt.manage"))
          ? promptEditor.setSection(sectionId) : Promise.resolve(),
        technicalAssignmentFilePicker.setSection(sectionId),
        userPackageCatalog.setSection(hasPermission(session, "user_documents.own.read") ? sectionId : null),
      ]);
    },
  },
);

bindMainIdentity({
  root: document.body, normativeCatalog, promptEditor, userPackageCatalog,
});


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


const reviewController = createReviewPersistence({ getCapabilities: reviewCapabilities });

const resultView = createResultView(
  requireElement(
    "[data-analysis-result]",
  ),
  {
    onReportRendered: (root, options) => {
      mountAuthorizedReview(reviewController, root, options);
      void analysisHistory.refresh();
    },
    onReportCleared: reviewController.clear,
  },
);


function getNormativeSelection() {
  const selection = (
    normativeCatalog.getSelection()
  );

  if (!selection) {
    return null;
  }

  const prompt = (hasPermission(currentSession(), "working_prompt.use") || hasPermission(currentSession(), "system_prompt.manage"))
    ? promptEditor.getOverride(selection.sectionId) : {};

  return {
    ...selection,
    ...(hasPermission(currentSession(), "user_documents.own.read")
      && userPackageCatalog.getSelection()?.documentIds.length
      ? { userPackageDocumentIds: userPackageCatalog.getSelection().documentIds } : {}),
    ...prompt,
  };
}


const analysisForm = createAnalysisForm({
  formElement: analysisFormElement,

  pdfInput: requireElement(
    "[data-pdf-input]",
  ),

  cadInput: requireElement(
    "[data-cad-input]",
  ),

  technicalAssignmentInput: (
    technicalAssignmentFilePicker.input
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

bindReportRestoration({ resultView, formElement: analysisFormElement, submit: analysisController.submit, canSubmit: () => !reviewController.hasPending() });

void normativeCatalog.start();
