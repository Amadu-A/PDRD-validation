// frontend/app.js

const form = document.getElementById("analysisForm");
const result = document.getElementById("result");
const modal = document.getElementById("modal");
const modalText = document.getElementById("modalText");
const submitButton = document.getElementById("submitButton");


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


function statusLabel(status) {
  if (status === "confirmed") {
    return "Подтверждено по найденной нормативной базе";
  }

  if (status === "needs_review") {
    return "Требует проверки инженером";
  }

  return status || "Не определён";
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
    `Страницы: ${payload.selected_pages.join(", ")}`,
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
              `  - ${source.source_file}, PDF стр. ${source.page}`
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
            const fixedLabel = source.verified_fixed
              ? "исправление подтверждено"
              : "исправление не подтверждено";

            lines.push(
              `  - ${source.project_id}/${source.issue_id}: `
              + `${source.issue_text} `
              + `(${fixedLabel}; BEFORE ${source.before_page}`
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


form.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

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

    setBusy(
      true,
      "Qwen3-VL 8B понимает лист, подбирает нормативы, "
      + "проверяет соответствие и формирует отчёт. "
      + "На текущем компьютере один лист может "
      + "обрабатываться несколько минут.",
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

      const raw = await response.text();

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
        throw new Error(
          `HTTP ${response.status}\n`
          + JSON.stringify(
            payload,
            null,
            2,
          ),
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