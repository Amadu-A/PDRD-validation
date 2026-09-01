// frontend/src/js/features/analysis/report.js

/**
 * Формирует текстовое представление результата анализа.
 */

import {
  sourceModeLabel,
  statusLabel,
} from "./labels.js";


function appendCadSummary(
  payload,
  lines,
) {
  const cad = payload.cad;

  if (!cad) {
    return;
  }

  if (cad.original_file_name) {
    lines.push(
      `CAD: ${cad.original_file_name}`,
    );
  }

  if (
    cad.original_format
    || cad.normalized_format
  ) {
    lines.push(
      "CAD-формат: "
      + `${cad.original_format || "?"}`
      + " → "
      + `${cad.normalized_format || "?"}`,
    );
  }

  if (cad.selected_layout) {
    lines.push(
      `CAD layout: ${cad.selected_layout}`,
    );
  }

  const entityCounts = (
    cad.machine_data?.entity_counts
  );

  if (entityCounts) {
    lines.push(
      `CAD entities: ${JSON.stringify(entityCounts)}`,
    );
  }

  if (cad.warnings?.length) {
    lines.push(
      "Предупреждения CAD:",
    );

    cad.warnings.forEach((warning) => {
      lines.push(
        `  - ${warning}`,
      );
    });
  }
}


function appendRenderSummary(
  payload,
  lines,
) {
  const render = payload.render;

  if (!render) {
    return;
  }

  if (render.image_base64) {
    lines.push(
      "Рендер листа: доступен",
    );
  }

  if (render.pdf_image_base64) {
    lines.push(
      "PDF-рендер: доступен",
    );
  }

  if (render.cad_image_base64) {
    lines.push(
      "CAD-рендер: доступен",
    );
  }
}


function appendProjectContextSummary(
  payload,
  lines,
) {
  const context = (
    payload.explanatory_note_context
  );

  if (!context?.enabled) {
    lines.push(
      "Контекст ПЗ: выключен",
    );

    return;
  }

  lines.push(
    "Контекст ПЗ: включён",
  );

  if (
    context.start_page
    && context.end_page
  ) {
    lines.push(
      "Диапазон ПЗ: "
      + `${context.start_page}-${context.end_page}`,
    );
  }

  if (
    context.pages_count !== null
    && context.pages_count !== undefined
  ) {
    lines.push(
      `Страниц ПЗ: ${context.pages_count}`,
    );
  }

  if (
    context.indexed_chunks !== null
    && context.indexed_chunks !== undefined
  ) {
    lines.push(
      "Фрагментов ПЗ проиндексировано: "
      + `${context.indexed_chunks}`,
    );
  }

  const warnings = (
    context.validation?.warnings
  );

  if (
    Array.isArray(warnings)
    && warnings.length
  ) {
    lines.push(
      `Предупреждений проверки ПЗ: ${warnings.length}`,
    );
  }
}


function appendNormativeSources(
  finding,
  lines,
) {
  const sources = (
    finding.normative_sources
    || finding.basis_sources
    || []
  );

  if (!sources.length) {
    return;
  }

  lines.push(
    "Нормативные источники:",
  );

  sources.forEach((source) => {
    const fileName = (
      source.source_file
      || source.file_name
      || source.source
      || "источник"
    );

    const page = (
      source.page
      ?? source.page_number
      ?? "?"
    );

    lines.push(
      `  - ${fileName}, стр. ${page}`,
    );
  });
}


function appendProjectContextSources(
  finding,
  lines,
) {
  const sources = (
    finding.project_context_sources
    || []
  );

  if (!sources.length) {
    return;
  }

  lines.push(
    "Контекст ПЗ:",
  );

  sources.forEach((source) => {
    lines.push(
      "  - "
      + `${source.source_id || "PZ"}`
      + `, стр. ${source.page ?? "?"}`
      + `, score=${source.score ?? "?"}`,
    );
  });
}


function renderFinding(
  finding,
  index,
  defaultPage,
) {
  const lines = [];

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

  lines.push(
    `${index + 1}. Лист/страница ${page}`,
  );

  lines.push(
    `Статус: ${statusLabel(finding.status)}`,
  );

  lines.push(
    `Категория: ${finding.category || "—"}`,
  );

  lines.push(
    `Уровень: ${finding.severity || "—"}`,
  );

  lines.push(
    `Замечание: ${comment}`,
  );

  if (finding.evidence) {
    lines.push(
      `Основание на листе: ${finding.evidence}`,
    );
  }

  if (finding.basis) {
    lines.push(
      `Нормативное основание: ${finding.basis}`,
    );
  }

  appendNormativeSources(
    finding,
    lines,
  );

  appendProjectContextSources(
    finding,
    lines,
  );

  lines.push(
    `Рекомендация: ${recommendation}`,
  );

  if (
    finding.confidence !== null
    && finding.confidence !== undefined
  ) {
    lines.push(
      `Уверенность: ${finding.confidence}`,
    );
  }

  return lines;
}


export function renderAnalysisReport(
  payload,
) {
  if (payload.status !== "completed") {
    return JSON.stringify(
      payload,
      null,
      2,
    );
  }

  const lines = [];

  lines.push(
    `Режим: ${sourceModeLabel(payload.source_mode)}`,
  );

  if (payload.pdf_file_name) {
    lines.push(
      `PDF: ${payload.pdf_file_name}`,
    );

  } else if (
    payload.file_name
    && payload.source_mode === "pdf_only"
  ) {
    lines.push(
      `PDF: ${payload.file_name}`,
    );
  }

  if (payload.cad_file_name) {
    lines.push(
      `DWG/DXF: ${payload.cad_file_name}`,
    );

  } else if (
    payload.file_name
    && payload.source_mode === "cad_only"
  ) {
    lines.push(
      `DWG/DXF: ${payload.file_name}`,
    );
  }

  if (payload.selected_pages?.length) {
    lines.push(
      `Страницы: ${payload.selected_pages.join(", ")}`,
    );
  }

  if (
    payload.analyzed_pages !== null
    && payload.analyzed_pages !== undefined
  ) {
    lines.push(
      `Проанализировано листов: ${payload.analyzed_pages}`,
    );
  }

  lines.push(
    `Замечаний: ${payload.findings_count ?? 0}`,
  );

  if (payload.summary) {
    lines.push(
      `Итог: ${payload.summary}`,
    );
  }

  appendProjectContextSummary(
    payload,
    lines,
  );

  appendCadSummary(
    payload,
    lines,
  );

  appendRenderSummary(
    payload,
    lines,
  );

  if (payload.pipeline?.length) {
    lines.push(
      `Pipeline: ${payload.pipeline.join(" → ")}`,
    );
  }

  lines.push("");

  const findings = (
    Array.isArray(payload.findings)
      ? payload.findings
      : []
  );

  if (!findings.length) {
    lines.push(
      "Замечания не сформированы.",
    );

  } else {
    lines.push(
      "ЗАМЕЧАНИЯ",
      "",
    );

    const defaultPage = (
      payload.page?.page_number
      ?? payload.selected_pages?.[0]
      ?? 1
    );

    findings.forEach(
      (finding, index) => {
        lines.push(
          ...renderFinding(
            finding,
            index,
            defaultPage,
          ),
        );

        lines.push("");
      },
    );
  }

  if (payload.limitations?.length) {
    lines.push(
      "Ограничения текущего MVP:",
    );

    payload.limitations.forEach(
      (limitation) => {
        lines.push(
          `- ${limitation}`,
        );
      },
    );
  }

  return lines.join("\n");
}