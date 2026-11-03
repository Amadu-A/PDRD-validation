// frontend/src/js/features/analysis/equipment-discovery.js

/** Отделяет ход поиска, применимость документа и доверие к сайту. */
export const EQ_STAGES = {
  not_searched: "Поиск не выполнялся", search_running: "Идёт поиск",
  search_temporarily_unavailable: "Поиск временно недоступен", candidates_found: "Найдены кандидаты",
  document_not_found: "Документ не найден", download_failed: "Ошибка загрузки",
  document_rejected: "Кандидаты отклонены", document_found: "Документ найден",
  document_verified: "Применимость проверена", facts_extracted: "Характеристики извлечены",
  comparison_completed: "Сравнение выполнено", comparison_unavailable: "Недостаточно данных для сравнения",
  budget_exhausted: "Бюджет исчерпан",
};

export const EQ_APPLICABILITY = {
  exact_match: "Точная модель и исполнение", family_match: "Семейство моделей",
  technical_reference: "Справочный документ", partial_match: "Частичное совпадение",
  wrong_model: "Другая модель", wrong_variant: "Другое исполнение",
  wrong_manufacturer: "Другой изготовитель", insufficient_evidence: "Недостаточно доказательств",
  unreadable_document: "Документ не прочитан",
};

export const EQ_ITEM_TYPES = {
  serial_equipment: "Серийное оборудование", standard_product: "Типовое изделие",
  material: "Материал", prefabricated_structure: "Сборная конструкция",
  designed_structure: "Проектируемая конструкция", unidentified: "Нужна идентификация",
};
