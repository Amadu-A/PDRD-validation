// frontend/src/js/features/analysis/api.js

/**
 * HTTP adapter публичного Analysis API Gateway.
 */

import {
  ANALYSES_ENDPOINT,
} from "../../config.js";


import { fetchJson } from "../../api.js";
import { guestAccessHeaders } from "./guest-access.js";
export { ApiError } from "../../api.js";

/** Каждый запрос к конкретному заданию передаёт его отдельное временное право. */
function jobRequest(jobId, suffix = "", options = {}) {
  return fetchJson(
    `${ANALYSES_ENDPOINT}/${encodeURIComponent(jobId)}${suffix}`,
    { ...options, cache: "no-store", headers: {
      ...guestAccessHeaders(jobId), ...options.headers,
    } },
  );
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


export async function cancelAnalysis(
  jobId,
) {
  return jobRequest(jobId, "/cancel", { method: "POST" });
}


export async function getAnalysisProgress(
  jobId,
) {
  return jobRequest(jobId, "/progress");
}


export async function getAnalysisStatus(
  jobId,
) {
  const statusPayload = await jobRequest(jobId);

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
  return jobRequest(jobId, "/result");
}


export async function getAnalysisVisualization(
  jobId,
) {
  return jobRequest(jobId, "/visualization");
}
