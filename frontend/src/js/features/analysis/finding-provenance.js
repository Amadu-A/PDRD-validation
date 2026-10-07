// frontend/src/js/features/analysis/finding-provenance.js

/** Происхождение берётся только из сохранённых массивов источников. */
export function findingSourceKinds(finding) {
  const arrays = [
    ["D", finding.document_context_basis_sources],
    ["N", finding.basis_sources ?? finding.normative_sources],
    ["T", finding.technical_assignment_basis_sources ?? finding.technical_assignment_sources],
    ["U", finding.user_package_basis_sources ?? finding.user_package_sources],
    ["E", finding.experience_sources],
  ];
  return arrays.filter(([, values]) => Array.isArray(values) && values.length).map(([kind]) => kind);
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

/** Рисует отдельный бейдж каждого подтверждённого типа. */
export function appendSourceBadges(parent, finding) {
  const kinds = findingSourceKinds(finding);
  const labels = { D: "Проверяемый PDF", N: "Норматив", T: "Техническое задание", U: "Пользовательские документы", E: "База Опыта" };
  for (const kind of kinds.length ? kinds : [null]) {
    const badge = document.createElement("span");
    badge.className = "analysis-result__badge analysis-result__source-kind";
    badge.textContent = kind ? `[${kind}]` : "Инженерное";
    badge.title = kind ? labels[kind] : "Замечание без сохранённых внешних источников";
    parent.append(badge);
  }
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
