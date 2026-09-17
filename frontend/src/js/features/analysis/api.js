// frontend/src/js/features/analysis/api.js

/**
 * HTTP adapter публичного Analysis API Gateway.
 */

import {
  ANALYSES_ENDPOINT,
} from "../../config.js";


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


export async function submitProjectContextPreflight(
  formData,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/project-context/preflight`,
    {
      method: "POST",
      body: formData,
    },
  );
}


export async function submitAnalysis(
  formData,
) {
  return fetchJson(
    ANALYSES_ENDPOINT,
    {
      method: "POST",
      body: formData,
    },
  );
}


export async function getAnalysisProgress(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/progress`,
  );
}


export async function getAnalysisStatus(
  jobId,
) {
  const statusPayload = await fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}`,
  );

  let progress = null;

  try {
    progress = await getAnalysisProgress(
      jobId,
    );

  } catch (error) {
    console.warn(
      "Не удалось получить analysis progress.",
      error,
    );
  }

  return {
    ...statusPayload,
    progress,
  };
}


export async function getAnalysisResult(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/result`,
  );
}


export async function getAnalysisVisualization(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/visualization`,
  );
}
