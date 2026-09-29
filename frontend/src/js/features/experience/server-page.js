// frontend/src/js/features/experience/server-page.js

/** Серверные фильтры и пагинация: поздний ответ не заменяет более новый запрос. */
import { experienceRow } from "./rows.js";
import { mountExperienceDialogs } from "./dialogs.js";
import { downloadReviewedPdf as downloadFile } from "../review/download.js";

export async function mountExperienceCatalog({ api, document: dom = document, download = downloadFile }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const filter = find("filter");
  const rows = find("rows");
  const count = find("count");
  const notice = find("notice");
  const exportButton = find("export");
  const previous = find("previous");
  const next = find("next");
  const refresh = find("refresh");
  let offset = 0;
  let total = 0;
  let generation = 0;
  let timer = null;
  let exporting = false;
  const limit = 50;
  const jobId = new URL(window.location.href).searchParams.get("job_id");
  if (jobId) filter.elements.namedItem("job_id").value = jobId;
  find("caption").textContent = "Сохранённые примеры Experience";
  dom.querySelector("[data-experience-pagination]").hidden = false;

  const criteria = () => Object.fromEntries(new FormData(filter).entries());
  const dialogs = mountExperienceDialogs({ api, onSaved: () => load(), document: dom });

  async function load(reset = false) {
    if (reset) offset = 0;
    const request = ++generation;
    exportButton.disabled = true; previous.disabled = true; next.disabled = true;
    rows.setAttribute("aria-busy", "true");
    notice.textContent = "Загружаем сохранённые замечания…";
    try {
      const result = await api.list({ ...criteria(), offset, limit });
      if (request !== generation) return;
      if (!Array.isArray(result.items) || !Number.isSafeInteger(result.total)) throw new Error("Сервер вернул некорректный каталог.");
      total = result.total;
      rows.replaceChildren(...result.items.map((example) => experienceRow(example,
        { api, onImage: dialogs.openImage, onEdit: dialogs.edit, onHistory: dialogs.history })));
      count.textContent = total ? `Показано ${offset + 1}–${offset + result.items.length} из ${total}.` : "Сохранённых примеров по этим фильтрам нет.";
      notice.textContent = "Это постоянная база опыта. Правки сохраняются с историей. Неактуальные источники и неоднозначные отклонения требуют проверки перед использованием для обучения.";
      exportButton.disabled = exporting;
      previous.disabled = offset === 0;
      next.disabled = offset + limit >= total;
    } catch (error) {
      if (request === generation) { notice.textContent = error.detail ?? error.message; rows.replaceChildren(); count.textContent = "Каталог не загружен. Нажмите «Обновить»."; }
    } finally { if (request === generation) rows.setAttribute("aria-busy", "false"); }
  }

  filter.addEventListener("submit", (event) => { event.preventDefault(); clearTimeout(timer); void load(true); });
  filter.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { void load(true); }, 300); });
  filter.addEventListener("change", () => { clearTimeout(timer); void load(true); });
  refresh.addEventListener("click", () => { clearTimeout(timer); void load(); });
  previous.addEventListener("click", () => { if (offset > 0) { offset -= limit; void load(); } });
  next.addEventListener("click", () => { if (offset + limit < total) { offset += limit; void load(); } });
  exportButton.addEventListener("click", async () => {
    if (exporting || exportButton.disabled) return;
    exporting = true; exportButton.disabled = true;
    try { download(await api.export(criteria())); notice.textContent = "ZIP содержит полный отфильтрованный набор, тексты, происхождение и PNG областей."; }
    catch (error) { notice.textContent = error.detail ?? error.message; }
    finally { exporting = false; exportButton.disabled = false; }
  });
  await load();
  return { load };
}
