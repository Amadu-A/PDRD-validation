// frontend/src/js/features/normative/section-selection.js

/** Выбирает раздел только по действующему явному выбору пользователя. */
export function retainSectionSelection(sections, currentId, preferredId = null) {
  const ids = new Set(sections.map((section) => section.section_id));
  if (preferredId && ids.has(preferredId)) return preferredId;
  if (currentId && ids.has(currentId)) return currentId;
  return null;
}

/** Возвращает подсказку, пока анализ не привязан к разделу документации. */
export function sectionSelectionError(selection) {
  return selection?.sectionId
    ? null
    : "Выберите раздел проектной документации";
}
