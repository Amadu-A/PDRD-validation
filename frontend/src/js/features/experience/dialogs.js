// frontend/src/js/features/experience/dialogs.js

/** Crop, инженерная редакция с CAS и неизменяемая история в отдельных модальных окнах. */
import { element } from "./rows.js";

const FIELDS = ["document_title", "text", "normative_basis", "normative_reference", "active", "rejection_reason", "negative_target"];

export function mountExperienceDialogs({ api, onSaved, document: dom = document }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const imageDialog = find("image-dialog");
  const image = find("large-image");
  const editDialog = find("edit-dialog");
  const form = find("edit-form");
  const error = find("edit-error");
  const save = find("edit-save");
  const reload = find("edit-reload");
  const historyDialog = find("history-dialog");
  const historyContent = find("history-content");
  let editing = null;
  let busy = false;
  let historyGeneration = 0;

  function fill(example) {
    editing = example;
    for (const key of FIELDS) {
      const field = form.elements.namedItem(key);
      if (key === "active") field.checked = example.requested_active;
      else field.value = example[key] ?? "";
    }
    find("rejection-fields").hidden = example.decision !== "rejected";
    find("source-text").textContent = `Исходный текст: ${example.source.original_text}\nТекст после Human Review: ${example.source.text}\nДокумент: ${example.document_id}\nЗадание: ${example.job_id}\nНаходка: ${example.source.finding_id}\nРевизия Review: ${example.source.approved_revision}\nОбласть проверил: ${example.source.confirmed_by}`;
    error.textContent = "";
    reload.hidden = true;
  }

  function edit(example) { fill(example); editDialog.showModal(); form.elements.namedItem("text").focus(); }
  function openImage(example, index) {
    image.src = api.imageUrl(example.id, index);
    image.alt = `Область ${index + 1}: ${example.text}`;
    find("image-original").href = image.src;
    imageDialog.showModal();
  }

  for (const name of ["text", "normative_basis"]) {
    form.elements.namedItem(name).addEventListener("input", () => {
      if (editing && ["revised", "both"].includes(editing.negative_target)) {
        form.elements.namedItem("negative_target").value = "";
        form.elements.namedItem("rejection_reason").value = "";
        error.textContent = "Формулировка изменена. Уточните отрицательный пример заново или оставьте «Ещё не определено».";
      }
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !editing) return;
    busy = true; save.disabled = true; reload.disabled = true;
    const example = editing;
    const fields = Object.fromEntries(FIELDS.map((key) => [key, key === "active"
      ? form.elements.namedItem(key).checked : form.elements.namedItem(key).value]));
    try {
      await api.update(example.id, example.revision, fields);
      if (editing?.id === example.id && editDialog.open) { editDialog.close(); await onSaved(); }
    } catch (failure) {
      if (editing?.id === example.id && editDialog.open) {
        error.textContent = failure.detail ?? failure.message;
        reload.hidden = failure.status !== 409;
      }
    } finally { busy = false; save.disabled = false; reload.disabled = false; }
  });
  reload.addEventListener("click", async () => {
    if (busy || !editing) return;
    busy = true; save.disabled = true; reload.disabled = true;
    const id = editing.id;
    try { const record = await api.get(id); if (editDialog.open && editing?.id === id) fill(record); }
    catch (failure) { if (editDialog.open) error.textContent = failure.detail ?? failure.message; }
    finally { busy = false; save.disabled = false; reload.disabled = false; }
  });
  find("image-close").addEventListener("click", () => { imageDialog.close(); image.removeAttribute("src"); });
  find("edit-close").addEventListener("click", () => { editDialog.close(); editing = null; });
  find("history-close").addEventListener("click", () => { historyGeneration += 1; historyDialog.close(); });

  async function history(example) {
    const generation = ++historyGeneration;
    historyContent.textContent = "Загружаем историю…";
    historyDialog.showModal();
    try {
      const result = await api.history(example.id);
      if (generation !== historyGeneration || !historyDialog.open) return;
      historyContent.replaceChildren(...result.events.map((event) => {
        const block = element("section", "experience-history");
        block.append(element("h3", "", `Редакция ${event.revision} · ${new Date(event.occurred_at).toLocaleString("ru")}`),
          element("p", "", `Автор: ${event.actor}`), element("p", "", event.snapshot.text),
          element("p", "", event.snapshot.normative_basis),
          element("p", "", event.snapshot.normative_reference),
          element("p", "", `Активность: ${event.snapshot.active ? "включена" : "выключена"}`),
          element("p", "", event.snapshot.rejection_reason || "Причина отказа не уточнялась"),
          element("p", "", `Отрицательная формулировка: ${{ original: "исходная", revised: "исправленная", both: "обе" }[event.snapshot.negative_target] ?? "не определена"}`));
        return block;
      }));
    } catch (failure) { if (generation === historyGeneration && historyDialog.open) historyContent.textContent = failure.detail ?? failure.message; }
  }
  return { edit, openImage, history };
}
