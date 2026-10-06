// frontend/src/js/features/account/capabilities.js

/** Поясняет уровни доступа, используя серверный список прав текущей сессии. */
const capabilities = [
  ["analysis.run", "Загрузка PDF/CAD, ТЗ, ПЗ и автоматический анализ"],
  ["analysis.result.download", "Скачивание результата анализа"],
  ["user_documents.own.write", "Собственные пользовательские документы (временно недоступны)"],
  ["review.gold.create", "Создание Gold-замечаний"],
  ["review.findings.decide", "Принятие и отклонение замечаний"],
  ["review.approve", "Утверждение Human Review"],
  ["normative.write", "Изменение нормативной базы"],
  ["experience.capture", "Сохранение утверждённого Review в Experience"],
  ["system_prompt.manage", "Системный промпт"],
  ["admin.access", "Администрирование PDRD"],
];
const unavailable = new Set(["user_documents.own.write"]);

export function partitionCapabilities(session) {
  const permissions = new Set(session.user?.permissions ?? [
    "analysis.run", "analysis.result.download",
  ]);
  return {
    allowed: capabilities.filter(([key]) => permissions.has(key) && !unavailable.has(key))
      .map(([, label]) => label),
    locked: capabilities.filter(([key]) => !permissions.has(key) || unavailable.has(key))
      .map(([, label]) => label),
  };
}

export function renderCapabilities(root, session) {
  const { allowed, locked } = partitionCapabilities(session);
  for (const [selector, values] of [
    ["[data-account-allowed]", allowed],
    ["[data-account-locked]", locked],
  ]) {
    const list = root.querySelector(selector);
    list.replaceChildren(...values.map((value) => {
      const item = document.createElement("li");
      item.textContent = value;
      return item;
    }));
  }
}
