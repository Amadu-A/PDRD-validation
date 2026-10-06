// frontend/src/js/features/analysis/history-api.js

/** Клиент собственной истории; UUID пользователя и гостевые ключи не передаются. */
import { fetchJson, fetchPdf } from "../../api.js";
import { createReviewApi } from "../review/api.js";

export const historyApi = {
  list: ({ limit = 20, offset = 0 } = {}) => fetchJson(
    `/api/v1/analyses/history?${new URLSearchParams({ limit, offset })}`,
    { cache: "no-store" },
  ),
  pdf: (item) => item.pdf_kind === "reviewed"
    ? createReviewApi().pdf(item.job_id, item.review_revision)
    : fetchPdf(`/api/v1/analyses/${encodeURIComponent(item.job_id)}/annotated-pdf`, { cache: "no-store" }),
};
