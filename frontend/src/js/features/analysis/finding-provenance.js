// frontend/src/js/features/analysis/finding-provenance.js

/** Согласует сохранённые источники и переходы к доказательствам замечания. */

/** Выбирает непустой сохранённый массив; пустой основной не скрывает резервный. */
export function preferredSourceArray(primary, fallback) {
  if (Array.isArray(primary) && primary.length) return primary;
  return Array.isArray(fallback) ? fallback : [];
}

/** Происхождение берётся только из сохранённых массивов источников. */
export function findingSourceKinds(finding) {
  const arrays = [
    ["D", finding.document_context_basis_sources],
    ["N", preferredSourceArray(finding.basis_sources, finding.normative_sources)],
    ["T", preferredSourceArray(finding.technical_assignment_basis_sources, finding.technical_assignment_sources)],
    ["U", preferredSourceArray(finding.user_package_basis_sources, finding.user_package_sources)],
    ["E", finding.experience_sources],
    ["EQ", finding.equipment_documentation_basis_sources],
  ];
  return arrays.filter(([, values]) => Array.isArray(values) && values.length).map(([kind]) => kind);
}

/** Объясняет происхождение в полном отчёте, не занимая место на чертеже. */
export function findingSourceDescription(finding) {
  const descriptions = {
    N: "N — сохранённое нормативное основание (ГОСТ, СП, ПУЭ и другие нормативы).",
    T: "T — сохранённое требование технического задания; это не нормативное основание.",
    U: "U — сохранённый источник из пользовательских документов; это не нормативное основание.",
    D: "D — факты проверяемого PDF. Они подтверждают содержание и внутренние противоречия, но не определяют правильное значение и не заменяют норматив.",
    E: "E — сохранённый инженерный опыт для повторной проверки и формулировки; он не доказывает нарушение.",
    EQ: "EQ — сохранённая техническая документация производителя для указанной модели и ревизии.",
  };
  const kinds = findingSourceKinds(finding);
  const lines = kinds.length
    ? kinds.map((kind) => descriptions[kind])
    : ["Инженерное замечание: сохранённые источники N/T/U/D/E отсутствуют. Вывод основан на данных листа и требует проверки инженером."];
  if (Array.isArray(finding.project_context_sources) && finding.project_context_sources.length) {
    lines.push("ПЗ — контекст пояснительной записки; он не является нормативным доказательством.");
  }
  return lines.join("\n");
}

/** Физические страницы одного логического замечания, без повторов. */
export function findingPages(finding, fallback = 1) {
  const pages = [finding.page ?? finding.page_number ?? fallback,
    ...(finding.evidence_locations ?? []).map((location) => location.page),
    ...(finding.document_context_basis_sources ?? []).map((source) => source.page)];
  return [...new Set(pages.map(Number).filter((page) => Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
}

/** Русская подпись для отчёта и карточек. */
export function findingPageLabel(finding, fallback = 1) {
  const pages = findingPages(finding, fallback);
  return `${pages.length > 1 ? "Страницы" : "Страница"} ${pages.join(", ")}`;
}

/** Переходит к странице и подсвечивает сохранённые области этого замечания. */
export function focusDocumentEvidence(link, findingId, pageNumber) {
  link.addEventListener("click", () => {
    const page = document.getElementById(`analysis-page-${pageNumber}`);
    if (!page) return;
    page.scrollIntoView({ behavior: "smooth", block: "start" });
    for (const node of page.querySelectorAll(".analysis-result__bbox")) {
      if (node.dataset.findingId !== String(findingId)) continue;
      node.classList.add("is-document-evidence-focus");
      window.setTimeout(() => node.classList.remove("is-document-evidence-focus"), 2500);
    }
  });
}

/** Строит путь к сохранённой версии только для канонических ID задания и EQ. */
export function equipmentSnapshotUrl(jobId, sourceId) {
  const job = String(jobId || "");
  const source = String(sourceId || "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(job)
      || !/^EQ-[0-9a-f]{32}$/i.test(source)) return null;
  return "/api/v1/analyses/" + encodeURIComponent(job)
    + "/equipment-documents/" + encodeURIComponent(source);
}
