// frontend/app.js

const form = document.getElementById("analysisForm");
const result = document.getElementById("result");
const modal = document.getElementById("modal");
const modalText = document.getElementById("modalText");
const submitButton = document.getElementById("submitButton");

const pdfFile = document.getElementById("pdfFile");
const cadFile = document.getElementById("cadFile");
const pages = document.getElementById("pages");
const pagesHint = document.getElementById("pagesHint");

const useExplanatoryNote = document.getElementById(
  "useExplanatoryNote",
);

const noteStartPage = document.getElementById(
  "noteStartPage",
);

const noteEndPage = document.getElementById(
  "noteEndPage",
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


function syncExplanatoryNoteFields() {
  const pdfAvailable = hasPdf();

  useExplanatoryNote.disabled = !pdfAvailable;

  if (!pdfAvailable) {
    useExplanatoryNote.checked = false;
  }

  const enabled = (
    pdfAvailable
    && useExplanatoryNote.checked
  );

  noteStartPage.disabled = !enabled;
  noteEndPage.disabled = !enabled;

  noteStartPage.required = enabled;
  noteEndPage.required = enabled;

  if (!enabled) {
    noteStartPage.value = "";
    noteEndPage.value = "";
    noteStartPage.setCustomValidity("");
    noteEndPage.setCustomValidity("");
  }
}


function syncSourceFields() {
  const mode = currentMode();

  pages.setCustomValidity("");

  if (mode === "cad_only") {
    pages.value = "";
    pages.disabled = true;
    pages.required = false;
    pages.placeholder = "Для CAD-only не используется";

    pagesHint.textContent = (
      "CAD-only: файл считается одним листом; поле страниц PDF отключено."
    );
  } else if (mode === "pdf_cad") {
    pages.disabled = false;
    pages.required = true;
    pages.placeholder = "Например: 11";

    pagesHint.textContent = (
      "PDF + CAD: укажите ровно одну страницу PDF, "
      + "которая соответствует загруженному DWG/DXF."
    );
  } else {
    pages.disabled = false;
    pages.required = false;
    pages.placeholder = (
      "PDF-only: 3,5,8-12. Пусто = весь PDF"
    );

    pagesHint.textContent = (
      "Для PDF-only можно анализировать одну или несколько страниц."
    );
  }

  syncExplanatoryNoteFields();
}


function validateExplanatoryNoteFields() {
  if (!useExplanatoryNote.checked) {
    return true;
  }

  const start = Number(
    noteStartPage.value,
  );

  const end = Number(
    noteEndPage.value,
  );

  noteStartPage.setCustomValidity("");
  noteEndPage.setCustomValidity("");

  if (
    !Number.isInteger(start)
    || start <= 0
  ) {
    noteStartPage.setCustomValidity(
      "Введите положительный номер начальной страницы.",
    );

    noteStartPage.reportValidity();
    return false;
  }

  if (
    !Number.isInteger(end)
    || end <= 0
  ) {
    noteEndPage.setCustomValidity(
      "Введите положительный номер конечной страницы.",
    );

    noteEndPage.reportValidity();
    return false;
  }

  if (end <= start) {
    noteEndPage.setCustomValidity(
      "Конечная страница ПЗ должна быть больше начальной.",
    );

    noteEndPage.reportValidity();
    return false;
  }

  return true;
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
        "При PDF + CAD укажите ровно одну положительную страницу PDF.",
      );

      pages.reportValidity();
      return false;
    }
  }

  return validateExplanatoryNoteFields();
}


function statusLabel(status) {
  if (status === "confirmed") {
    return "Подтверждено по найденной нормативной базе";
  }

  if (status === "needs_review") {
    return "Требует проверки инженером";
  }

  return status || "Не определён";
}


function sourceModeLabel(mode) {
  if (mode === "pdf_cad") {
    return "PDF + DWG/DXF";
  }

  if (mode === "cad_only") {
    return "DWG/DXF без PDF";
  }

  return "PDF";
}


function renderExplanatoryNoteSummary(
  payload,
  lines,
) {
  const context = (
    payload.explanatory_note_context
  );

  if (!context?.enabled) {
    lines.push(
      "Контекст ПЗ: не использовался",
    );
    return;
  }

  lines.push(
    `Контекст ПЗ: страницы `
    + `${context.start_page}-${context.end_page}`,
  );

  lines.push(
    `ПЗ проиндексирована временно: `
    + `${context.indexed_chunks} фрагм.`,
  );

  const warnings = (
    context.validation?.warnings || []
  );

  if (warnings.length) {
    lines.push(
      "Предупреждения по диапазону ПЗ:",
    );

    warnings.forEach(
      (warning) => {
        lines.push(
          `  - стр. ${warning.page}: `
          + `${warning.kind}; `
          + `${warning.reason}`,
        );
      },
    );
  }
}


function renderCadSummary(
  payload,
  lines,
) {
  if (!payload.cad) {
    return;
  }

  lines.push(
    `CAD: ${payload.cad.original_file_name}`,
  );

  lines.push(
    `CAD-формат: ${payload.cad.original_format.toUpperCase()}`
    + ` → ${payload.cad.normalized_format.toUpperCase()}`,
  );

  lines.push(
    `CAD layout: ${payload.cad.selected_layout}`,
  );

  lines.push(
    `CAD entities: ${payload.cad.expanded_entity_count}`,
  );

  lines.push(
    `CAD dangling endpoints: `
    + `${payload.cad.geometry?.dangling_endpoint_count ?? 0}`,
  );

  if (payload.cad.warnings?.length) {
    lines.push(
      "Предупреждения CAD:",
    );

    payload.cad.warnings.forEach(
      (warning) => {
        lines.push(
          `  - ${warning}`,
        );
      },
    );
  }
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
  }

  if (payload.cad_file_name) {
    lines.push(
      `DWG/DXF: ${payload.cad_file_name}`,
    );
  }

  lines.push(
    `VLM: ${payload.vision_model}`,
  );

  lines.push(
    `Embeddings: ${payload.embedding_model}`,
  );

  if (payload.source_mode !== "cad_only") {
    lines.push(
      `Страницы PDF: `
      + `${payload.selected_pages.join(", ")}`,
    );
  } else {
    lines.push(
      "CAD-лист: 1",
    );
  }

  renderExplanatoryNoteSummary(
    payload,
    lines,
  );

  renderCadSummary(
    payload,
    lines,
  );

  lines.push(
    `Время анализа: ${payload.elapsed_seconds} сек.`,
  );

  lines.push(
    `Всего замечаний: ${payload.issues_count}`,
  );

  lines.push(
    `Подтверждено: ${payload.confirmed_count}`,
  );

  lines.push(
    `Требует проверки: ${payload.needs_review_count}`,
  );

  lines.push("");

  if (!payload.issues?.length) {
    lines.push(
      "По найденным нормативным требованиям "
      + "замечаний не сформировано.",
    );

    lines.push("");
  }

  payload.issues?.forEach(
    (issue, index) => {
      lines.push(
        `${index + 1}. Лист/страница ${issue.page}`,
      );

      lines.push(
        `Источник: ${sourceModeLabel(issue.source_mode)}`,
      );

      lines.push(
        `Статус: ${statusLabel(issue.status)}`,
      );

      lines.push(
        `Категория: ${issue.category}`,
      );

      lines.push(
        `Уровень: ${issue.severity}`,
      );

      lines.push(
        `Замечание: ${issue.comment}`,
      );

      lines.push(
        `Основание на листе: ${issue.evidence}`,
      );

      if (
        issue.project_context_sources?.length
      ) {
        const pzPages = [
          ...new Set(
            issue.project_context_sources
              .map(
                (source) => source.page,
              )
              .filter(
                (page) =>
                  page !== null
                  && page !== undefined,
              ),
          ),
        ];

        lines.push(
          `Учтён контекст ПЗ со страниц: `
          + `${pzPages.join(", ")}`,
        );
      }

      if (issue.basis) {
        lines.push(
          `Нормативное основание: ${issue.basis}`,
        );
      }

      if (issue.basis_sources?.length) {
        lines.push(
          "Найденные нормативные фрагменты:",
        );

        issue.basis_sources.forEach(
          (source) => {
            lines.push(
              `  - ${source.source_file}, `
              + `PDF стр. ${source.page}`
              + `; similarity=${source.score}`,
            );
          },
        );
      }

      lines.push(
        `Рекомендация: ${issue.recommendation}`,
      );

      if (issue.experience_sources?.length) {
        lines.push(
          "Похожий экспертный опыт:",
        );

        issue.experience_sources.forEach(
          (source) => {
            const fixedLabel = (
              source.verified_fixed
                ? "исправление подтверждено"
                : "исправление не подтверждено"
            );

            lines.push(
              `  - ${source.project_id}/${source.issue_id}: `
              + `${source.issue_text} `
              + `(${fixedLabel}; `
              + `BEFORE ${source.before_page}`
              + ` → AFTER ${source.after_page})`,
            );
          },
        );
      }

      lines.push(
        `Уверенность: ${issue.confidence}`,
      );

      lines.push("");
    },
  );

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


pdfFile.addEventListener(
  "change",
  syncSourceFields,
);

cadFile.addEventListener(
  "change",
  syncSourceFields,
);

useExplanatoryNote.addEventListener(
  "change",
  syncExplanatoryNoteFields,
);

noteStartPage.addEventListener(
  "input",
  () => {
    noteStartPage.setCustomValidity("");
    noteEndPage.setCustomValidity("");
  },
);

noteEndPage.addEventListener(
  "input",
  () => {
    noteEndPage.setCustomValidity("");
  },
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
    const mode = currentMode();

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

    body.append(
      "pages",
      pages.disabled
        ? ""
        : pages.value.trim(),
    );

    body.append(
      "use_explanatory_note",
      String(
        useExplanatoryNote.checked,
      ),
    );

    if (useExplanatoryNote.checked) {
      body.append(
        "note_start_page",
        noteStartPage.value,
      );

      body.append(
        "note_end_page",
        noteEndPage.value,
      );
    }

    let endpoint;

    if (mode === "pdf_cad") {
      endpoint = "/api/analysis/pdf-cad";
    } else if (mode === "cad_only") {
      endpoint = "/api/analysis/cad";
    } else {
      endpoint = "/api/analysis/pdf";
    }

    const contextMessage = (
      useExplanatoryNote.checked
        ? " Сначала будет проверен и временно проиндексирован диапазон ПЗ."
        : ""
    );

    const cadMessage = (
      cad
        ? " CAD будет нормализован в DXF, распарсен и отрендерен."
        : ""
    );

    setBusy(
      true,
      "Qwen3-VL анализирует визуальное представление, "
      + "машинные CAD-данные и нормативную базу."
      + cadMessage
      + contextMessage,
    );

    result.textContent = (
      "Анализ выполняется…"
    );

    try {
      const response = await fetch(
        endpoint,
        {
          method: "POST",
          body,
        },
      );

      const raw = await response.text();

      let payload;

      try {
        payload = JSON.parse(raw);
      } catch {
        payload = {raw};
      }

      if (!response.ok) {
        const detail = (
          payload?.detail
            ? (
              typeof payload.detail === "string"
                ? payload.detail
                : JSON.stringify(
                  payload.detail,
                  null,
                  2,
                )
            )
            : JSON.stringify(
              payload,
              null,
              2,
            )
        );

        throw new Error(
          `HTTP ${response.status}\n${detail}`,
        );
      }

      result.textContent = renderReport(
        payload,
      );
    } catch (error) {
      result.textContent = (
        `Ошибка:\n${error.message}`
      );
    } finally {
      setBusy(false);
    }
  },
);
