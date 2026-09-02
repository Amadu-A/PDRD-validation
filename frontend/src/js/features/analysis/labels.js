// frontend/src/js/features/analysis/labels.js

/**
 * Человекочитаемые названия domain-значений анализа.
 */

const STATUS_LABELS = {
  pending: "Заявка принята",
  queued: "Ожидает обработки",
  processing: "Выполняется анализ",
  completed: "Анализ завершён",
  failed: "Ошибка анализа",
  cancelled: "Анализ отменён",

  confirmed: "Подтверждено",
  needs_review: "Требует проверки инженером",
};


const SOURCE_MODE_LABELS = {
  pdf_only: "PDF",
  cad_only: "DWG/DXF",
  pdf_cad: "PDF + DWG/DXF",
};


export function statusLabel(status) {
  return (
    STATUS_LABELS[status]
    || status
    || "Не определён"
  );
}


export function sourceModeLabel(mode) {
  return (
    SOURCE_MODE_LABELS[mode]
    || mode
    || "Не определён"
  );
}