// frontend/src/js/api.js

/** Общий HTTP-клиент: сохраняет статус и понятное описание серверной ошибки. */

export class ApiError extends Error {
  /**
   * @param {number} status HTTP status.
   * @param {string} detail Человекочитаемый detail.
   */
  constructor(
    status,
    detail,
  ) {
    super(
      `HTTP ${status}\n${detail}`,
    );

    this.name = "ApiError";

    this.status = status;

    this.detail = detail;
  }
}


export async function fetchJson(
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
      payload = JSON.parse(raw);

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

    throw new ApiError(
      response.status,
      detail,
    );
  }

  return payload;
}

/** Скачивает только настоящий PDF; JSON/HTML ошибки не превращаются в файл. */
export async function fetchPdf(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const raw = await response.text();
    let detail = raw;
    try { detail = JSON.parse(raw).detail ?? raw; } catch { /* Ответ прокси может быть текстовым. */ }
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const blob = await response.blob();
  const signature = await blob.slice(0, 5).text();
  if (signature !== "%PDF-" || !response.headers.get("content-type")?.startsWith("application/pdf")) {
    throw new ApiError(503, "Сервер не вернул итоговый PDF.");
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  let filename = "reviewed.pdf";
  if (encoded) {
    try { filename = decodeURIComponent(encoded); } catch { /* Используем безопасное имя по умолчанию. */ }
  }
  return { blob, filename };
}

