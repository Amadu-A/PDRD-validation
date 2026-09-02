// frontend/src/js/features/analysis/api.js

/**
 * HTTP adapter публичного Analysis API Gateway.
 */

import {
  ANALYSES_ENDPOINT,
} from "../../config.js";


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

    throw new Error(
      `HTTP ${response.status}\n${detail}`,
    );
  }

  return payload;
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


export async function getAnalysisStatus(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}`,
  );
}


export async function getAnalysisResult(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/result`,
  );
}