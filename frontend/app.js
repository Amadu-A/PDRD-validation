// frontend/app.js

const form = document.getElementById("analysisForm");
const result = document.getElementById("result");
const modal = document.getElementById("modal");
const modalText = document.getElementById("modalText");
const submitButton = document.getElementById("submitButton");

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


function syncExplanatoryNoteFields() {
  const enabled = useExplanatoryNote.checked;

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


function statusLabel(status) {
  if (status === "confirmed") {
    return "Подтверждено по найденной нормативной базе";
  }

  if (status === "needs_review") {
    return "Требует проверки инженером";
  }

  return status || "Не определён";
}


function renderExplanatoryNoteSummary(
  payload,
  lines,
) {
  const context =
    payload.explanatory_note_context;

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

  const warnings =
    context.validation?.warnings || [];

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
    `Файл: ${payload.file_name}`,
  );

  lines.push(
    `VLM: ${payload.vision_model}`,
  );

  lines.push(
    `Embeddings: ${payload.embedding_model}`,
  );

  lines.push(
    `Страницы: `
    + `${payload.selected_pages.join(", ")}`,
  );

  renderExplanatoryNoteSummary(
    payload,
    lines,
  );

  lines.push(
    `Время анализа: `
    + `${payload.elapsed_seconds} сек.`,
  );

  lines.push(
    `Всего замечаний: `
    + `${payload.issues_count}`,
  );

  lines.push(
    `Подтверждено: `
    + `${payload.confirmed_count}`,
  );

  lines.push(
    `Требует проверки: `
    + `${payload.needs_review_count}`,
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
        `${index + 1}. Страница ${issue.page}`,
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
        `Что видно на листе: ${issue.evidence}`,
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
            const fixedLabel =
              source.verified_fixed
                ? "исправление подтверждено"
                : "исправление не подтверждено";

            lines.push(
              `  - ${source.project_id}/`
              + `${source.issue_id}: `
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

syncExplanatoryNoteFields();


form.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

    if (!validateExplanatoryNoteFields()) {
      return;
    }

    const pdf = document
      .getElementById("pdfFile")
      .files[0];

    const pages = document
      .getElementById("pages")
      .value
      .trim();

    if (!pdf) {
      result.textContent =
        "Выберите PDF-файл.";

      return;
    }

    const body = new FormData();

    body.append(
      "pdf",
      pdf,
    );

    body.append(
      "pages",
      pages,
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

    const contextMessage =
      useExplanatoryNote.checked
        ? (
          " Сначала будет проверен "
          + "и временно проиндексирован "
          + "диапазон ПЗ."
        )
        : "";

    setBusy(
      true,
      "Qwen3-VL понимает лист, "
      + "подбирает нормативы, "
      + "проверяет соответствие "
      + "и формирует отчёт."
      + contextMessage,
    );

    result.textContent =
      "Анализ выполняется…";

    try {
      const response = await fetch(
        "/api/analysis",
        {
          method: "POST",
          body,
        },
      );

      const raw =
        await response.text();

      let payload;

      try {
        payload = JSON.parse(
          raw,
        );
      } catch {
        payload = {
          raw,
        };
      }

      if (!response.ok) {
        const detail =
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
            );

        throw new Error(
          `HTTP ${response.status}\n${detail}`,
        );
      }

      result.textContent =
        renderReport(
          payload,
        );

    } catch (error) {
      result.textContent =
        `Ошибка:\n${error.message}`;

    } finally {
      setBusy(false);
    }
  },
);