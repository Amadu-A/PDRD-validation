// frontend/src/js/features/review/history.js

/**
 * История обратимых локальных действий одного листа.
 * Хранит обратные операции, переданные UI-контроллером, без DOM и API.
 * Ошибка отмены сохраняет действие в истории для повторной попытки.
 */

/** Создаёт независимый журнал создания, удаления и изменения областей Gold. */
export function createManualHistory() {
  const actions = [];

  return {
    /** Регистрирует только уже успешно сохранённое локальное действие. */
    record(label, undo) {
      actions.push({ label, undo });
    },

    /** Отменяет последнее действие; при ошибке сохраняет его в истории. */
    undo() {
      const action = actions.at(-1);
      if (!action) return false;
      action.undo();
      actions.pop();
      return true;
    },

    /** Название последнего действия для доступного текста кнопки. */
    get label() {
      return actions.at(-1)?.label ?? "";
    },
  };
}
