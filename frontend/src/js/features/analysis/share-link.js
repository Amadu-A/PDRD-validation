// frontend/src/js/features/analysis/share-link.js

/** Показывает временную ссылку на результат после закрытия окна анализа. */
import { guestAccessFor, temporaryAnalysisLink } from "./guest-access.js";

export function appendGuestShareLink(report, jobId) {
  const access = guestAccessFor(jobId);
  const url = access?.expiresAt ? temporaryAnalysisLink(jobId) : null;
  if (!url) return;
  const section = document.createElement("section");
  section.className = "analysis-share";
  const title = document.createElement("h3");
  title.textContent = "Повторное открытие результата";
  const description = document.createElement("p");
  description.textContent = `Ссылка действует до ${new Date(access.expiresAt).toLocaleString("ru-RU")}. Каждый, у кого она есть, сможет открыть и скачать результат.`;
  const link = document.createElement("a");
  link.href = url;
  link.textContent = "Открыть или скопировать временную ссылку";
  section.append(title, description, link);
  report.append(section);
}
