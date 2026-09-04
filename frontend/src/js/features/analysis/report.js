// frontend/src/js/features/analysis/report.js

/**
 * Строит безопасное DOM-представление результата анализа.
 *
 * Пользовательские и модельные строки вставляются только через textContent.
 * Нормативные и пользовательские citations открывают managed PDF
 * или Word PDF-preview на физической странице из Knowledge Service.
 */

import {
  sourceModeLabel,
  statusLabel,
} from "./labels.js";


const CATEGORY_LABELS = {
  normative_control: "Нормоконтроль",
  equipment: "Оборудование",
  scheme_logic: "Логика схемы",
  marking: "Маркировка",
  completeness: "Комплектность",
  optimization: "Оптимизация",
  customer_requirements: "Требования заказчика",
  other: "Прочее",
};


const SEVERITY_LABELS = {
  info: "Информация",
  warning: "Предупреждение",
  error: "Ошибка",
};


function createElement(
  tagName,
  className = "",
  text = null,
) {
  const node = document.createElement(
    tagName,
  );

  if (className) {
    node.className = className;
  }

  if (
    text !== null
    && text !== undefined
  ) {
    node.textContent = String(
      text,
    );
  }

  return node;
}


function appendMetaItem(
  list,
  label,
  value,
  {
    code = false,
  } = {},
) {
  if (
    value === null
    || value === undefined
    || value === ""
  ) {
    return;
  }

  const item = createElement(
    "div",
    "analysis-result__meta-item",
  );

  const term = createElement(
    "dt",
    "analysis-result__meta-label",
    label,
  );

  const description = createElement(
    "dd",
    "analysis-result__meta-value",
  );

  if (code) {
    description.append(
      createElement(
        "code",
        "analysis-result__job-id",
        value,
      ),
    );

  } else {
    description.textContent = String(
      value,
    );
  }

  item.append(
    term,
    description,
  );

  list.append(
    item,
  );
}


function appendTextBlock(
  parent,
  label,
  value,
) {
  if (
    value === null
    || value === undefined
    || value === ""
  ) {
    return;
  }

  const block = createElement(
    "div",
    "analysis-result__field",
  );

  block.append(
    createElement(
      "strong",
      "analysis-result__field-label",
      label,
    ),
  );

  block.append(
    createElement(
      "p",
      "analysis-result__field-value",
      value,
    ),
  );

  parent.append(
    block,
  );
}


function normalizePage(
  value,
) {
  if (
    typeof value === "number"
    && Number.isInteger(
      value,
    )
    && value >= 1
  ) {
    return value;
  }

  if (
    typeof value === "string"
    && /^\d+$/.test(
      value.trim(),
    )
  ) {
    const page = Number.parseInt(
      value,
      10,
    );

    if (page >= 1) {
      return page;
    }
  }

  return null;
}


function sourceFileName(
  source,
  fallback,
) {
  return (
    source.source_file
    || source.file_name
    || source.source
    || fallback
  );
}


function uniqueSources(
  sources,
) {
  if (!Array.isArray(
    sources,
  )) {
    return [];
  }

  const seen = new Set();

  return sources.filter(
    (source) => {
      if (
        !source
        || typeof source !== "object"
      ) {
        return false;
      }

      const key = [
        source.document_id || "",
        source.page ?? "",
        source.point_id || "",
        source.source_file || "",
      ].join(
        ":",
      );

      if (seen.has(
        key,
      )) {
        return false;
      }

      seen.add(
        key,
      );

      return true;
    },
  );
}


function preferredSourceArray(
  primary,
  fallback,
) {
  if (
    Array.isArray(
      primary,
    )
    && primary.length
  ) {
    return primary;
  }

  if (Array.isArray(
    fallback,
  )) {
    return fallback;
  }

  return [];
}


function normativeSources(
  finding,
) {
  return uniqueSources(
    preferredSourceArray(
      finding.normative_sources,
      finding.basis_sources,
    ),
  );
}


function userPackageSources(
  finding,
) {
  return uniqueSources(
    preferredSourceArray(
      finding.user_package_basis_sources,
      finding.user_package_sources,
    ),
  );
}


function managedCitationUrl(
  source,
  pathPrefix,
) {
  const documentId = (
    typeof source.document_id === "string"
      ? source.document_id.trim()
      : ""
  );

  const page = normalizePage(
    source.page
    ?? source.page_number,
  );

  if (
    !documentId
    || page === null
  ) {
    return null;
  }

  return (
    pathPrefix
    + `${encodeURIComponent(documentId)}`
    + `/content#page=${page}`
  );
}


function createManagedCitation(
  source,
  {
    fallbackName,
    pathPrefix,
    datasetName,
    titlePrefix,
  },
) {
  const fileName = sourceFileName(
    source,
    fallbackName,
  );

  const page = normalizePage(
    source.page
    ?? source.page_number,
  );

  const label = (
    page === null
      ? fileName
      : `${fileName}, стр. ${page}`
  );

  const url = managedCitationUrl(
    source,
    pathPrefix,
  );

  if (!url) {
    return createElement(
      "span",
      "analysis-result__source-text",
      label,
    );
  }

  const link = createElement(
    "a",
    "analysis-result__source-link",
    label,
  );

  link.href = url;

  link.target = "_blank";

  link.rel = (
    "noopener noreferrer"
  );

  if (datasetName === "normativeCitation") {
    link.dataset.normativeCitation = "";
  }

  if (datasetName === "userPackageCitation") {
    link.dataset.userPackageCitation = "";
  }

  link.dataset.documentId = (
    source.document_id
  );

  link.dataset.page = String(
    page,
  );

  link.title = (
    `${titlePrefix} на странице ${page}`
  );

  return link;
}


function createNormativeCitation(
  source,
) {
  return createManagedCitation(
    source,
    {
      fallbackName: "Нормативный источник",
      pathPrefix: "/api/v1/normative/documents/",
      datasetName: "normativeCitation",
      titlePrefix: "Открыть нормативный документ",
    },
  );
}


function createUserPackageCitation(
  source,
) {
  return createManagedCitation(
    source,
    {
      fallbackName: "Пользовательский документ",
      pathPrefix: (
        "/api/v1/normative/"
        + "user-packages/documents/"
      ),
      datasetName: "userPackageCitation",
      titlePrefix: "Открыть пользовательский документ",
    },
  );
}


function appendSourceList(
  parent,
  label,
  sources,
  createCitation,
) {
  if (!sources.length) {
    return;
  }

  const section = createElement(
    "div",
    "analysis-result__sources",
  );

  section.append(
    createElement(
      "strong",
      "analysis-result__field-label",
      label,
    ),
  );

  const list = createElement(
    "ul",
    "analysis-result__source-list",
  );

  sources.forEach(
    (source) => {
      const item = createElement(
        "li",
        "analysis-result__source-item",
      );

      item.append(
        createCitation(
          source,
        ),
      );

      list.append(
        item,
      );
    },
  );

  section.append(
    list,
  );

  parent.append(
    section,
  );
}


function appendNormativeSources(
  finding,
  parent,
) {
  appendSourceList(
    parent,
    "Нормативные источники",
    normativeSources(
      finding,
    ),
    createNormativeCitation,
  );
}


function appendUserPackageSources(
  finding,
  parent,
) {
  appendSourceList(
    parent,
    "Пользовательские требования / документы",
    userPackageSources(
      finding,
    ),
    createUserPackageCitation,
  );
}


function appendProjectContextSources(
  finding,
  parent,
) {
  const sources = (
    Array.isArray(
      finding.project_context_sources,
    )
      ? finding.project_context_sources
      : []
  );

  if (!sources.length) {
    return;
  }

  const section = createElement(
    "div",
    "analysis-result__sources",
  );

  section.append(
    createElement(
      "strong",
      "analysis-result__field-label",
      "Контекст ПЗ",
    ),
  );

  const list = createElement(
    "ul",
    "analysis-result__source-list",
  );

  sources.forEach(
    (source) => {
      const page = (
        source.page
        ?? "?"
      );

      const sourceId = (
        source.source_id
        || "PZ"
      );

      const score = (
        source.score
        ?? "?"
      );

      list.append(
        createElement(
          "li",
          "analysis-result__source-item",
          `${sourceId}, стр. ${page}, score=${score}`,
        ),
      );
    },
  );

  section.append(
    list,
  );

  parent.append(
    section,
  );
}


function appendFinding(
  finding,
  index,
  defaultPage,
  parent,
) {
  const page = (
    finding.page
    ?? finding.page_number
    ?? defaultPage
    ?? 1
  );

  const comment = (
    finding.comment
    || finding.message
    || finding.issue_text
    || "Текст замечания не передан."
  );

  const recommendation = (
    finding.recommendation
    || finding.recommendation_draft
    || "Не указана."
  );

  const article = createElement(
    "article",
    "analysis-result__finding",
  );

  const header = createElement(
    "div",
    "analysis-result__finding-header",
  );

  header.append(
    createElement(
      "h4",
      "analysis-result__finding-title",
      `${index + 1}. Лист/страница ${page}`,
    ),
  );

  const badges = createElement(
    "div",
    "analysis-result__badges",
  );

  badges.append(
    createElement(
      "span",
      "analysis-result__badge",
      statusLabel(
        finding.status,
      ),
    ),
  );

  badges.append(
    createElement(
      "span",
      "analysis-result__badge",
      (
        SEVERITY_LABELS[
          finding.severity
        ]
        || finding.severity
        || "Уровень не указан"
      ),
    ),
  );

  header.append(
    badges,
  );

  article.append(
    header,
  );

  appendTextBlock(
    article,
    "Категория",
    (
      CATEGORY_LABELS[
        finding.category
      ]
      || finding.category
      || "—"
    ),
  );

  appendTextBlock(
    article,
    "Замечание",
    comment,
  );

  appendTextBlock(
    article,
    "Основание на листе",
    finding.evidence,
  );

  appendTextBlock(
    article,
    "Нормативное основание",
    finding.basis,
  );

  appendNormativeSources(
    finding,
    article,
  );

  appendUserPackageSources(
    finding,
    article,
  );

  appendProjectContextSources(
    finding,
    article,
  );

  appendTextBlock(
    article,
    "Рекомендация",
    recommendation,
  );

  if (
    finding.confidence !== null
    && finding.confidence !== undefined
  ) {
    appendTextBlock(
      article,
      "Уверенность",
      finding.confidence,
    );
  }

  parent.append(
    article,
  );
}


function appendProjectContextSummary(
  payload,
  list,
) {
  const context = (
    payload.explanatory_note_context
  );

  if (!context?.enabled) {
    appendMetaItem(
      list,
      "Контекст ПЗ",
      "Выключен",
    );

    return;
  }

  appendMetaItem(
    list,
    "Контекст ПЗ",
    "Включён",
  );

  if (
    context.start_page
    && context.end_page
  ) {
    appendMetaItem(
      list,
      "Диапазон ПЗ",
      `${context.start_page}-${context.end_page}`,
    );
  }

  if (
    context.pages_count !== null
    && context.pages_count !== undefined
  ) {
    appendMetaItem(
      list,
      "Страниц ПЗ",
      context.pages_count,
    );
  }

  if (
    context.indexed_chunks !== null
    && context.indexed_chunks !== undefined
  ) {
    appendMetaItem(
      list,
      "Фрагментов ПЗ",
      context.indexed_chunks,
    );
  }
}


function appendCadSummary(
  payload,
  list,
) {
  const cad = payload.cad;

  if (!cad) {
    return;
  }

  if (cad.original_file_name) {
    appendMetaItem(
      list,
      "CAD",
      cad.original_file_name,
    );
  }

  if (
    cad.original_format
    || cad.normalized_format
  ) {
    appendMetaItem(
      list,
      "CAD-формат",
      (
        `${cad.original_format || "?"}`
        + " → "
        + `${cad.normalized_format || "?"}`
      ),
    );
  }

  if (cad.selected_layout) {
    appendMetaItem(
      list,
      "CAD layout",
      cad.selected_layout,
    );
  }
}


function appendOverview(
  payload,
  jobId,
  parent,
) {
  const section = createElement(
    "section",
    "analysis-result__summary",
  );

  section.append(
    createElement(
      "h3",
      "analysis-result__section-title",
      "Сводка анализа",
    ),
  );

  const list = createElement(
    "dl",
    "analysis-result__meta",
  );

  if (jobId) {
    appendMetaItem(
      list,
      "Задание",
      jobId,
      {
        code: true,
      },
    );
  }

  appendMetaItem(
    list,
    "Режим",
    sourceModeLabel(
      payload.source_mode,
    ),
  );

  const pdfFileName = (
    payload.pdf_file_name
    || (
      payload.source_mode === "pdf_only"
        ? payload.file_name
        : null
    )
  );

  appendMetaItem(
    list,
    "PDF",
    pdfFileName,
  );

  const cadFileName = (
    payload.cad_file_name
    || (
      payload.source_mode === "cad_only"
        ? payload.file_name
        : null
    )
  );

  appendMetaItem(
    list,
    "DWG/DXF",
    cadFileName,
  );

  if (
    Array.isArray(
      payload.selected_pages,
    )
    && payload.selected_pages.length
  ) {
    appendMetaItem(
      list,
      "Страницы",
      payload.selected_pages.join(
        ", ",
      ),
    );
  }

  if (
    payload.analyzed_pages !== null
    && payload.analyzed_pages !== undefined
  ) {
    appendMetaItem(
      list,
      "Проанализировано листов",
      payload.analyzed_pages,
    );
  }

  appendMetaItem(
    list,
    "Замечаний",
    payload.findings_count ?? 0,
  );

  appendProjectContextSummary(
    payload,
    list,
  );

  appendCadSummary(
    payload,
    list,
  );

  if (
    Array.isArray(
      payload.pipeline,
    )
    && payload.pipeline.length
  ) {
    appendMetaItem(
      list,
      "Pipeline",
      payload.pipeline.join(
        " → ",
      ),
    );
  }

  section.append(
    list,
  );

  if (payload.summary) {
    appendTextBlock(
      section,
      "Итог",
      payload.summary,
    );
  }

  parent.append(
    section,
  );
}


function appendFindings(
  payload,
  parent,
) {
  const section = createElement(
    "section",
    "analysis-result__findings",
  );

  section.append(
    createElement(
      "h3",
      "analysis-result__section-title",
      "Замечания",
    ),
  );

  const findings = (
    Array.isArray(
      payload.findings,
    )
      ? payload.findings
      : []
  );

  if (!findings.length) {
    section.append(
      createElement(
        "p",
        "analysis-result__empty",
        "Замечания не сформированы.",
      ),
    );

    parent.append(
      section,
    );

    return;
  }

  const defaultPage = (
    payload.page?.page_number
    ?? payload.selected_pages?.[0]
    ?? 1
  );

  findings.forEach(
    (
      finding,
      index,
    ) => {
      appendFinding(
        finding,
        index,
        defaultPage,
        section,
      );
    },
  );

  parent.append(
    section,
  );
}


function appendLimitations(
  payload,
  parent,
) {
  if (
    !Array.isArray(
      payload.limitations,
    )
    || !payload.limitations.length
  ) {
    return;
  }

  const section = createElement(
    "section",
    "analysis-result__limitations",
  );

  section.append(
    createElement(
      "h3",
      "analysis-result__section-title",
      "Ограничения текущего MVP",
    ),
  );

  const list = createElement(
    "ul",
    "analysis-result__limitation-list",
  );

  payload.limitations.forEach(
    (limitation) => {
      list.append(
        createElement(
          "li",
          "analysis-result__limitation",
          limitation,
        ),
      );
    },
  );

  section.append(
    list,
  );

  parent.append(
    section,
  );
}


export function renderAnalysisReport(
  payload,
  {
    jobId = null,
  } = {},
) {
  const fragment = (
    document.createDocumentFragment()
  );

  if (payload.status !== "completed") {
    fragment.append(
      createElement(
        "p",
        "analysis-result__message",
        (
          jobId
            ? (
              `Задание: ${jobId}\n`
              + JSON.stringify(
                payload,
                null,
                2,
              )
            )
            : JSON.stringify(
              payload,
              null,
              2,
            )
        ),
      ),
    );

    return fragment;
  }

  appendOverview(
    payload,
    jobId,
    fragment,
  );

  appendFindings(
    payload,
    fragment,
  );

  appendLimitations(
    payload,
    fragment,
  );

  return fragment;
}