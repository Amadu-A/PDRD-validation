// frontend/src/js/features/technical_assignment/form-payload.js

/** Передаёт подготовленный ТЗ и его HMAC-доступ при запуске анализа. */
import { technicalAssignmentAccessFor } from "./access.js";

export function technicalAssignmentAccessError(input) {
  if (input.dataset.technicalAssignmentAccessRequired !== "true") return null;
  return technicalAssignmentAccessFor(input.dataset.technicalAssignmentId)
    ? null : "Срок доступа к подготовленному ТЗ истёк. Загрузите файл повторно.";
}

export function appendTechnicalAssignmentPayload(body, input) {
  const file = input.files[0];
  if (!file) return;
  body.append("technical_assignment", file);
  body.append("technical_assignment_id", input.dataset.technicalAssignmentId);
  body.append("technical_assignment_analysis_document_id", input.dataset.analysisDocumentId);
  const token = technicalAssignmentAccessFor(input.dataset.technicalAssignmentId);
  if (token) body.append("technical_assignment_access_token", token);
}
