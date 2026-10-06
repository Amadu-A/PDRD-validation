// frontend/src/js/features/technical_assignment/citation.js

/** Открывает PDF-preview ТЗ через Blob с HMAC-доступом только в HTTP заголовке. */
import { fetchPdf } from "../../api.js";
import { technicalAssignmentAccessHeaders } from "./access.js";

export async function openTechnicalAssignmentCitation(id, page, browser = window) {
  // Пустую вкладку резервируем в самом click, иначе браузер блокирует async popup.
  const tab = browser.open("about:blank", "_blank");
  if (!tab) throw new Error("Разрешите открытие вкладки для просмотра ТЗ.");
  tab.opener = null;
  try {
    const { blob } = await fetchPdf(
      `/api/v1/normative/technical-assignments/${encodeURIComponent(id)}/content`,
      { headers: technicalAssignmentAccessHeaders(id), credentials: "same-origin", cache: "no-store" },
    );
    const url = URL.createObjectURL(blob);
    tab.location.href = `${url}#page=${page}`;
    browser.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    tab.close();
    throw error;
  }
}
