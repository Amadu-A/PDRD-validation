// frontend/src/js/features/review/api.js

/** HTTP Review через Gateway. Инженер и служебные ключи задаются сервером. */

import { fetchJson } from "../../api.js";
import { ANALYSES_ENDPOINT } from "../../config.js";

function endpoint(jobId, suffix = "") {
  return `${ANALYSES_ENDPOINT}/${encodeURIComponent(jobId)}/review${suffix}`;
}

/** Создаёт заменяемый клиент для браузера и тестов синхронизации. */
export function createReviewApi() {
  return {
    config: () => fetchJson("/api/v1/review/config", { cache: "no-store" }),
    open: (jobId) => fetchJson(endpoint(jobId, "/open"), { method: "POST", cache: "no-store" }),
    read: (jobId) => fetchJson(endpoint(jobId), { cache: "no-store" }),
    command: (jobId, command) => fetchJson(endpoint(jobId, "/commands"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command), cache: "no-store",
    }),
  };
}
