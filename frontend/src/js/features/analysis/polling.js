// frontend/src/js/features/analysis/polling.js

/**
 * Polling жизненного цикла asynchronous analysis job.
 */

import {
  ANALYSIS_MAX_POLL_ATTEMPTS,
  ANALYSIS_POLL_INTERVAL_MS,
} from "../../config.js";

import {
  getAnalysisStatus,
} from "./api.js";


function sleep(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(
      resolve,
      milliseconds,
    );
  });
}


export async function waitForAnalysis(
  jobId,
  {
    onProgress = () => {},
  } = {},
) {
  const startedAt = Date.now();

  for (
    let attempt = 0;
    attempt < ANALYSIS_MAX_POLL_ATTEMPTS;
    attempt += 1
  ) {
    const payload = await getAnalysisStatus(
      jobId,
    );

    const elapsedSeconds = Math.floor(
      (
        Date.now()
        - startedAt
      )
      / 1000,
    );

    onProgress({
      payload,
      elapsedSeconds,
    });

    if (payload.status === "completed") {
      return payload;
    }

    if (payload.status === "failed") {
      throw new Error(
        payload.error_message
        || payload.error_code
        || "Анализ завершился ошибкой.",
      );
    }

    if (payload.status === "cancelled") {
      throw new Error(
        "Анализ был отменён.",
      );
    }

    await sleep(
      ANALYSIS_POLL_INTERVAL_MS,
    );
  }

  throw new Error(
    "Превышено максимальное время ожидания анализа.",
  );
}