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
    `Модель: ${payload.model}`,
  );

  lines.push(
    `Страницы: ${payload.selected_pages.join(", ")}`,
  );

  lines.push(
    `Время анализа: ${payload.elapsed_seconds} сек.`,
  );

  lines.push(
    `Найдено замечаний: ${payload.issues_count}`,
  );

  lines.push("");

  if (!payload.issues.length) {
    lines.push(
      "Модель не сформировала замечаний "
      + "по выбранным страницам.",
    );
  }

  payload.issues.forEach(
    (issue, index) => {
      lines.push(
        `${index + 1}. Страница ${issue.page}`,
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

      lines.push(
        `Рекомендация: ${issue.recommendation}`,
      );

      if (issue.basis) {
        lines.push(
          `Нормативное основание: ${issue.basis}`,
        );
      }

      lines.push(
        `Уверенность: ${issue.confidence}`,
      );

      lines.push("");
    },
  );

  if (payload.limitations?.length) {
    lines.push("Ограничения текущего этапа:");

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

    const dxf = document
      .getElementById("dxfFile")
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

    if (dxf) {
      body.append(
        "dxf",
        dxf,
      );
    }

    body.append(
      "pages",
      pages,
    );

    setBusy(
      true,
      "Qwen3-VL анализирует PDF. "
      + "На текущей GPU это может занять несколько минут.",
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

      result.textContent = renderReport(
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