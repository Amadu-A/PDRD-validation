// frontend/src/js/features/experience/version-actions.js

/**
 * Выгрузка неизменяемого набора версии и отправка отчёта независимой оценки.
 * Сервер проверяет метрики и принадлежность отчёта: UI не выдаёт готовность
 * коллекции или успешные тесты приложения за допуск рабочего Experience.
 */
const MAX_REPORT_BYTES = 1024 * 1024;

/** Читает ограниченный JSON-файл; окончательная проверка отчёта остаётся серверной. */
export async function readExperienceQualityReport(file) {
  if (!file) throw new Error("Выберите JSON-отчёт независимой оценки.");
  if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_REPORT_BYTES) {
    throw new Error("JSON-отчёт должен быть непустым и не превышать 1 МиБ.");
  }
  const text = await file.text();
  if (new TextEncoder().encode(text).byteLength > MAX_REPORT_BYTES) throw new Error("JSON-отчёт превышает 1 МиБ.");
  let report;
  try { report = JSON.parse(text); }
  catch { throw new Error("Файл не содержит корректный JSON-отчёт."); }
  if (!report || typeof report !== "object" || Array.isArray(report)) throw new Error("JSON-отчёт должен содержать объект.");
  return report;
}

/** Подключает действия выбранной версии, используя общую блокировку мутаций реестра. */
export function mountExperienceVersionActions({ api, current, isBusy, setBusy, onUpdated, repaint,
  download, notice, document: dom = document }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const dataset = find("version-dataset"), file = find("quality-file");
  const submit = find("quality-submit"), revoke = find("quality-revoke");
  let previousId = null;

  /** Доступность опирается на серверную версию и не изменяет выбор строк каталога. */
  function paint() {
    const version = current();
    if (version?.id !== previousId) { file.value = ""; previousId = version?.id ?? null; }
    const available = Boolean(version && !version.deleted);
    const qualityAvailable = available && version.kind === "vector" && version.status === "ready";
    dataset.disabled = !available || isBusy();
    find("quality-controls").hidden = !qualityAvailable;
    file.disabled = !qualityAvailable || isBusy();
    submit.disabled = !qualityAvailable || isBusy();
    revoke.disabled = !qualityAvailable || isBusy() || !(version.quality_approved || version.quality_report);
  }

  /** Сохраняет выбранную редакцию на весь запрос и возвращает ошибки в доступный статус. */
  async function execute(operation, message) {
    const version = current();
    if (!version || version.deleted || isBusy()) return;
    setBusy(true); repaint();
    try {
      const result = await operation(version);
      if (result) await onUpdated(result);
      notice.textContent = message;
    } catch (error) { notice.textContent = error.detail ?? error.message; }
    finally { setBusy(false); repaint(); }
  }

  dataset.addEventListener("click", () => {
    if (dataset.disabled) return;
    return execute(async (version) => { download(await api.exportVersion(version.id)); },
      "ZIP содержит фиксированные редакции и изображения этой версии. Текущие фильтры каталога его состав не меняют.");
  });
  submit.addEventListener("click", () => {
    if (submit.disabled) return;
    const selectedFile = file.files?.[0];
    return execute(async (version) => {
      const report = await readExperienceQualityReport(selectedFile);
      return api.approveVersionQuality(version.id, version.revision, report);
    }, "Отчёт проверен сервером. Результат допуска указан в статусе версии; загрузка отчёта не включает E автоматически.");
  });
  revoke.addEventListener("click", () => {
    if (revoke.disabled) return;
    return execute((version) => api.revokeVersionQuality(version.id, version.revision),
      "Допуск качества отозван. Эта версия недоступна для рабочего применения.");
  });
  return { paint };
}
