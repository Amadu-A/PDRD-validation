// frontend/src/js/features/experience/capture.js

/**
 * Перенос Experience в сценарии скачивания утверждённого PDF, без отдельного DOM.
 * Учитывает автоматический результат approve; повторное скачивание идемпотентно
 * завершает перенос сохранённой редакции. Ошибка crop/SQL не отменяет Review.
 */

/** Возвращает результат переноса, сохраняя возможность получить утверждённый PDF. */
export async function captureApprovedExperience({ jobId, session, approvedNow, api }) {
  if (approvedNow && session.experience_capture) return session.experience_capture;
  try {
    return { ...await api.capture(jobId, session.revision), status: "saved" };
  } catch (error) {
    return { status: "error", message: error.detail ?? error.message ?? "Не удалось сохранить примеры в базу опыта." };
  }
}

/** Объясняет сохранение только подходящих примеров и повтор при частичном сбое. */
export function experienceCaptureMessage(result) {
  if (result.status === "saved") return `В базу опыта сохранены проверенные примеры: ${result.eligible}; исключено без подтверждённой области или решения: ${result.excluded}.`;
  return `${result.message} Повторите скачивание итогового PDF, чтобы завершить сохранение в базу опыта.`;
}
