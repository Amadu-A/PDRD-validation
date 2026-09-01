// frontend/app.js

const ANALYSES_ENDPOINT = "/api/v1/analyses";
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 1800;

const form = document.getElementById(
  "analysisForm",
);

const result = document.getElementById(
  "result",
);

const modal = document.getElementById(
  "modal",
);

const modalText = document.getElementById(
  "modalText",
);

const submitButton = document.getElementById(
  "submitButton",
);

const pdfFile = document.getElementById(
  "pdfFile",
);

const cadFile = document.getElementById(
  "cadFile",
);

const pages = document.getElementById(
  "pages",
);

const pagesHint = document.getElementById(
  "pagesHint",
);


function setBusy(
  isBusy,
  text = "Идёт анализ документа…",
) {
  modalText.textContent = text;

  modal.classList.toggle(
    "hidden",
    !isBusy,
  );

  modal.setAttribute(
    "aria-hidden",
    String(!isBusy),
  );

  submitButton.disabled = isBusy;
}


function hasPdf() {
  return Boolean(
    pdfFile.files[0],
  );
}


function hasCad() {
  return Boolean(
    cadFile.files[0],
  );
}


function currentMode() {
  if (hasPdf() && hasCad()) {
    return "pdf_cad";
  }

  if (hasPdf()) {
    return "pdf_only";
  }

  if (hasCad()) {
    return "cad_only";
  }

  return "empty";
}


function sourceModeLabel(mode) {
  if (mode === "pdf_cad") {
    return "PDF + DWG/DXF";
  }

  if (mode === "cad_only") {
    return "DWG/DXF";
  }

  if (mode === "pdf_only") {
    return "PDF";
  }

  return mode || "Не определён";
}


function statusLabel(status) {
  const labels = {
    pending: "Заявка принята",
    queued: "Ожидает обработки",
    processing: "Выполняется анализ",
    completed: "Анализ завершён",
    failed: "Ошибка анализа",
    cancelled: "Анализ отменён",
    confirmed: "Подтверждено",
    needs_review: "Требует проверки инженером",
  };

  return labels[status]
    || status
    || "Не определён";
}


function syncSourceFields() {
  const mode = currentMode();

  pages.setCustomValidity("");

  if (mode === "cad_only") {
    pages.value = "";
    pages.disabled = true;
    pages.required = false;

    pages.placeholder = (
      "Для CAD-only не используется"
    );

    pagesHint.textContent = (
      "CAD-only: DWG/DXF считается одним листом."
    );

    return;
  }

  if (mode === "pdf_cad") {
    pages.disabled = false;
    pages.required = true;

    pages.placeholder = (
      "Например: 11"
    );

    pagesHint.textContent = (
      "PDF + CAD: укажите ровно одну страницу PDF, "
      + "соответствующую загруженному DWG/DXF."
    );

    return;
  }

  pages.disabled = false;
  pages.required = false;

  pages.placeholder = (
    "PDF-only: 3,5,8-12. Пусто = весь PDF"
  );

  pagesHint.textContent = (
    "Для PDF-only можно анализировать "
    + "одну или несколько страниц."
  );
}


function validateSources() {
  const mode = currentMode();

  pages.setCustomValidity("");

  if (mode === "empty") {
    result.textContent = (
      "Загрузите PDF и/или DWG/DXF."
    );

    return false;
  }

  if (mode === "pdf_cad") {
    const normalized = pages.value.trim();

    if (!/^[1-9]\d*$/.test(normalized)) {
      pages.setCustomValidity(
        "При PDF + CAD укажите ровно одну "
        + "положительную страницу PDF.",
      );

      pages.reportValidity();

      return false;
    }
  }

  return true;
}


function sleep(milliseconds) {
  return new Promise(
    (resolve) => {
      window.setTimeout(
        resolve,
        milliseconds,
      );
    },
  );
}


async function fetchJson(
  url,
  options = {},
) {
  const response = await fetch(
    url,
    options,
  );

  const raw = await response.text();

  let payload = {};

  if (raw) {
    try {
      payload = JSON.parse(
        raw,
      );
    } catch {
      payload = {
        raw,
      };
    }
  }

  if (!response.ok) {
    let detail = payload?.detail;

    if (
      detail
      && typeof detail !== "string"
    ) {
      detail = JSON.stringify(
        detail,
        null,
        2,
      );
    }

    if (!detail) {
      detail = (
        payload?.raw
        || JSON.stringify(
          payload,
          null,
          2,
        )
      );
    }

    throw new Error(
      `HTTP ${response.status}\n${detail}`,
    );
  }

  return payload;
}


async function submitAnalysis(
  body,
) {
  return fetchJson(
    ANALYSES_ENDPOINT,
    {
      method: "POST",
      body,
    },
  );
}


async function waitForAnalysis(
  jobId,
) {
  const startedAt = Date.now();

  for (
    let attempt = 0;
    attempt < MAX_POLL_ATTEMPTS;
    attempt += 1
  ) {
    const statusPayload = await fetchJson(
      `${ANALYSES_ENDPOINT}/${jobId}`,
    );

    const status = statusPayload.status;

    const elapsedSeconds = Math.floor(
      (
        Date.now()
        - startedAt
      )
      / 1000,
    );

    const message = (
      `${statusLabel(status)}. `
      + `Прошло ${elapsedSeconds} сек.`
    );

    setBusy(
      true,
      message,
    );

    result.textContent = (
      `Задание: ${jobId}\n`
      + `Статус: ${statusLabel(status)}\n`
      + `Попытка worker: `
      + `${statusPayload.attempt_count ?? 0}\n`
      + `Прошло: ${elapsedSeconds} сек.`
    );

    if (status === "completed") {
      return statusPayload;
    }

    if (status === "failed") {
      throw new Error(
        statusPayload.error_message
        || statusPayload.error_code
        || "Анализ завершился ошибкой.",
      );
    }

    if (status === "cancelled") {
      throw new Error(
        "Анализ был отменён.",
      );
    }

    await sleep(
      POLL_INTERVAL_MS,
    );
  }

  throw new Error(
    "Превышено максимальное время ожидания анализа.",
  );
}


async function loadAnalysisResult(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/result`,
  );
}


function pushCadSummary(
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
      "CAD entities: "
      + JSON.stringify(
        entityCounts,
      ),
    );
  }

  if (cad.warnings?.length) {
    lines.push(
      "Предупреждения CAD:",
    );

    cad.warnings.forEach(
      (warning) => {
        lines.push(
          `  - ${warning}`,
        );
      },
    );
  }
}


function pushRenderSummary(
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


function pushNormativeSources(
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

  sources.forEach(
    (source) => {
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
    },
  );
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

  pushNormativeSources(
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


function renderReport(payload) {
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

  pushCadSummary(
    payload,
    lines,
  );

  pushRenderSummary(
    payload,
    lines,
  );

  if (payload.pipeline?.length) {
    lines.push(
      "Pipeline: "
      + payload.pipeline.join(
        " → ",
      ),
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
    );

    lines.push("");

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

  return lines.join(
    "\n",
  );
}


pdfFile.addEventListener(
  "change",
  syncSourceFields,
);

cadFile.addEventListener(
  "change",
  syncSourceFields,
);

pages.addEventListener(
  "input",
  () => {
    pages.setCustomValidity("");
  },
);

syncSourceFields();


form.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

    if (!validateSources()) {
      return;
    }

    const pdf = pdfFile.files[0];
    const cad = cadFile.files[0];

    const body = new FormData();

    if (pdf) {
      body.append(
        "pdf",
        pdf,
      );
    }

    if (cad) {
      body.append(
        "cad",
        cad,
      );
    }

    if (
      !pages.disabled
      && pages.value.trim()
    ) {
      body.append(
        "pages",
        pages.value.trim(),
      );
    }

    result.textContent = (
      "Отправляем документы в API Gateway…"
    );

    setBusy(
      true,
      "Документы загружаются в API Gateway…",
    );

    try {
      const accepted = await submitAnalysis(
        body,
      );

      const jobId = accepted.job_id;

      if (!jobId) {
        throw new Error(
          "API Gateway не вернул job_id.",
        );
      }

      result.textContent = (
        `Задание создано: ${jobId}\n`
        + `Статус: ${statusLabel(accepted.status)}`
      );

      await waitForAnalysis(
        jobId,
      );

      setBusy(
        true,
        "Анализ завершён. Загружаем результат…",
      );

      const payload = await loadAnalysisResult(
        jobId,
      );

      result.textContent = renderReport(
        payload,
      );

    } catch (error) {
      result.textContent = (
        `Ошибка:\n${error.message}`
      );

    } finally {
      setBusy(
        false,
      );
    }
  },
);