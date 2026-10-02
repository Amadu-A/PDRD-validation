// frontend/src/js/features/analysis/automatic-pdf-download.js

/** Скачивает автоматический PDF с guest token в заголовке, не в URL или логах. */
import { fetchPdf } from "../../api.js";
import { guestAccessHeaders } from "./guest-access.js";

export async function downloadAutomaticPdf(jobId) {
  const { blob, filename } = await fetchPdf(
    `/api/v1/analyses/${encodeURIComponent(jobId)}/annotated-pdf`,
    { headers: guestAccessHeaders(jobId), credentials: "same-origin", cache: "no-store" },
  );
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
