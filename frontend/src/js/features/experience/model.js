// frontend/src/js/features/experience/model.js

/**
 * Локальная модель демонстрационной страницы Experience.
 * Переход к серверному хранению будет выполнен через отдельный API-адаптер.
 */

export const DEMO_EXAMPLES = Object.freeze([
  {
    id: "demo-wise",
    document_title: "Пример: план этажа",
    source_filename: "primer-plana.pdf",
    page_number: 2,
    text: "Проверить обозначение противопожарной двери.",
    normative_basis: "Пример нормативного раздела 1.2",
    tag: "wise",
    decision: "accepted",
    learning_use: "positive",
    active: true,
    crop_label: "Пример области на плане",
  },
  {
    id: "demo-edited",
    document_title: "Пример: схема инженерных сетей",
    source_filename: "primer-shemy.pdf",
    page_number: 4,
    text: "Уточнить расположение условного обозначения.",
    normative_basis: "Пример нормативного раздела 3.4",
    tag: "edited",
    decision: "rejected",
    learning_use: "needs_adjudication",
    active: false,
    crop_label: "Пример области на схеме",
  },
]);

/** Создаёт копию демонстрационных записей без общих изменяемых объектов. */
export function createExperienceModel(examples = DEMO_EXAMPLES) {
  const records = examples.map((example) => ({ ...example }));

  function list({ query = "", tag = "", decision = "", active = "" } = {}) {
    const needle = String(query).trim().toLocaleLowerCase("ru");
    return records.filter((item) => (
      (!needle || [item.text, item.document_title, item.source_filename,
        item.normative_basis].some((value) => (
        value.toLocaleLowerCase("ru").includes(needle)
      )))
      && (!tag || item.tag === tag)
      && (!decision || item.decision === decision)
      && (active === "" || String(item.active) === active)
    )).map((item) => ({ ...item }));
  }

  function update(id, { text, normative_basis, active }) {
    const record = records.find((item) => item.id === id);
    if (!record) {
      throw new Error("Запись Experience не найдена.");
    }
    const nextText = String(text ?? "").trim();
    const nextBasis = String(normative_basis ?? "").trim();
    if (!nextText || nextText.length > 10000 || nextBasis.length > 2000) {
      throw new Error("Проверьте текст и нормативное основание.");
    }
    if (typeof active !== "boolean") {
      throw new Error("Активность должна быть указана явно.");
    }
    record.text = nextText;
    record.normative_basis = nextBasis;
    record.active = active;
    return { ...record };
  }

  function get(id) {
    const record = records.find((item) => item.id === id);
    return record ? { ...record } : null;
  }

  return { list, update, get };
}
