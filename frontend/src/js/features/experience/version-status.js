// frontend/src/js/features/experience/version-status.js

/**
 * Представление состояния версии для каталога: сборка, допуск качества и применение.
 * Использует только серверные сведения; подготовленный набор не выдаётся за обученные
 * веса, а успешная индексация сама по себе не подтверждает улучшение анализа.
 */
const STATUS = {
  queued: "в очереди", building: "индексируется", ready: "готова",
  failed: "ошибка", prepared: "набор подготовлен",
};

/** Объясняет состояние артефакта и возвращает UI-допуск, совпадающий с серверной проверкой. */
export function experienceVersionStatus(version) {
  const label = STATUS[version.status] ?? "неизвестное состояние";
  const weightsMissing = version.kind === "fine_tune" && !version.weights_sha256;
  const ready = version.status === "ready";
  const approved = version.quality_approved === true;
  const canApply = ready && approved && !weightsMissing;
  const build = version.kind === "fine_tune"
    ? `Подготовка набора: ${label}. ${weightsMissing ? "Обученные веса отсутствуют; подготовка набора не запускает обучение." : "В реестре указаны обученные веса."}`
    : `Индексация: ${label}. ${ready ? "Коллекция Qdrant создана; доступен проверочный поиск." : "Коллекция ещё не готова к использованию."}`;
  const quality = approved
    ? "Качество: сервер подтвердил допуск этой версии."
    : "Качество: независимая проверка и допуск ещё не завершены.";
  let application = "Сервер допускает назначение в реестре. Подключение выбранных версий к рабочему анализу ещё не реализовано.";
  if (weightsMissing) application = "Применение недоступно: набор данных ещё не является дообученной моделью.";
  else if (!ready) application = "Применение недоступно: сначала необходимо завершить подготовку версии.";
  else if (!approved) application = "Применение недоступно: готовность коллекции не заменяет проверку качества на отдельном наборе документов.";
  return { label, build, quality, application, canApply };
}
