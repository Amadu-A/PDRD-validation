// frontend/src/js/features/review/pdf-model.js

/** Чистые правила доступности итогового PDF и явного подтверждения VLM-областей. */

export function reviewedPdfAvailability({ mode, pending = 0, session, busy = false }) {
  if (mode === "local") return { enabled: false, message: "Итоговый PDF доступен на закрытом фронте Review." };
  if (busy || mode !== "saved" || pending) return { enabled: false, message: "Дождитесь сохранения Review. При ошибке загрузите серверную версию." };
  if (!session || session.pending_count !== 0) return { enabled: false, message: "Сначала примите или отклоните все замечания." };
  return { enabled: true, message: "Принятые замечания попадут на листы с проверенной областью и в текстовый список. Замечания без подтверждённой области — только в текстовый список." };
}

export function areaConfirmationCommand(finding, status, regions, note = "") {
  if (finding.origin !== "vlm" || !regions.length) throw new Error("У замечания нет области для проверки.");
  const proposals = finding.proposed_regions.map((area) => area.bbox);
  const sameBox = (left, right) => ["x_min", "y_min", "x_max", "y_max"].every((key) => left[key] === right[key]);
  const proposed = regions.every((box) => proposals.some((candidate) => sameBox(candidate, box)));
  if (!proposed && !note.trim()) throw new Error("Для изменённой области укажите причину исправления.");
  return { action: "confirm_area", finding_id: finding.finding_id,
    expected_confirmation_revision: status?.revision ?? 0, regions,
    mode: proposed ? "proposed" : "redrawn", note: note.trim() };
}
