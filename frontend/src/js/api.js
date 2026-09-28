// frontend/src/js/api.js

/** ????? JSON HTTP-??????: ?????????? ?????? ??????? ? ?????? ??????. */

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


