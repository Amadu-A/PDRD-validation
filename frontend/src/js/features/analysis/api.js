// frontend/src/js/features/analysis/api.js

/**
 * HTTP adapter публичного Analysis API Gateway.
 */

import {
  ANALYSES_ENDPOINT,
} from "../../config.js";


import { fetchJson } from "../../api.js";
export { ApiError } from "../../api.js";

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


export async function cancelAnalysis(
  jobId,
) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${jobId}/cancel`,
    {
      method: "POST",
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