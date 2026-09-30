// frontend/src/js/features/review/pdf-model.js

/** Доступность итогового PDF: принятие замечания одновременно принимает его область. */

/** Старое принятое Review получает аудит областей при нажатии той же кнопки PDF. */
export function requiresAreaAcceptance(session) {
  const confirmed = new Set((session.area_confirmations ?? []).filter((area) => area.valid).map((area) => area.finding_id));
  return (session.findings ?? []).some((finding) => finding.origin === "vlm"
    && finding.decision === "accepted"
    && (finding.display_regions ?? finding.proposed_regions ?? []).length > 0
    && !confirmed.has(finding.finding_id));
}

export function reviewedPdfAvailability({ mode, pending = 0, session, busy = false }) {
  if (mode === "local") return { enabled: false, message: "Серверный Review недоступен. Итоговый PDF можно скачать после восстановления сервиса." };
  if (session?.pending_count > 0) return { enabled: false, message: `Сначала примите или отклоните все замечания. Ожидают решения: ${session.pending_count}.` };
  if (busy || mode !== "saved" || pending) return { enabled: false, message: "Дождитесь сохранения Review. При ошибке загрузите серверную версию." };
  if (!session || session.pending_count !== 0) return { enabled: false, message: "Сначала примите или отклоните все замечания." };
  return { enabled: true, message: "Принятые замечания попадут на листы с проверенной областью и в текстовый список. Замечания без подтверждённой области — только в текстовый список." };
}
