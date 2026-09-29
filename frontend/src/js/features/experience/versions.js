// frontend/src/js/features/experience/versions.js

/** Реестр: один вид подсветки, фиксированный состав и отдельное применение версии. */
import { element } from "./rows.js";

const STATUS = { queued: "в очереди", building: "индексируется", ready: "готова", failed: "ошибка", prepared: "набор подготовлен" };
const DEFAULT_MODELS = { vector: "shared-embedding", fine_tune: "shared-vlm" };

export function mountExperienceVersions({ api, selection, onViewed, notice, document: dom = document }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const kind = find("version-kind"), model = find("version-model"), choice = find("version");
  const dialog = find("version-dialog"), form = find("version-form"), save = find("version-save");
  let versions = [], applied = [], viewing = null, editing = false, busy = false, generation = 0, registryGeneration = 0;
  const report = (error) => { notice.textContent = error.detail ?? error.message; };
  function options(select, items, placeholder) {
    const current = select.value;
    const first = element("option", "", placeholder); first.value = "";
    select.replaceChildren(first, ...items.map(({ value, label, disabled = false }) => {
      const option = element("option", "", label); option.value = value; option.disabled = disabled; return option;
    }));
    select.value = items.some((item) => item.value === current) ? current : "";
  }
  function paint() {
    const models = [...new Set([DEFAULT_MODELS[kind.value], ...versions.filter((item) => item.kind === kind.value).map((item) => item.model)])];
    options(model, models.map((value) => ({ value, label: value })), "Все модели");
    const visible = versions.filter((item) => item.kind === kind.value && (!model.value || item.model === model.value));
    options(choice, visible.map((item) => ({ value: item.id, label: `${item.name} · ${STATUS[item.status] ?? item.status} · ${new Date(item.created_at).toLocaleDateString("ru")}` })), "Все замечания");
    find("version-rename").disabled = !viewing || busy;
    find("version-delete").disabled = !viewing || busy;
    find("build-version").textContent = kind.value === "vector" ? "Запуск индексации выбранных замечаний" : "Подготовить выбранные для дообучения";
    for (const [type, key] of [["vector", "active-vector"], ["fine_tune", "active-model"]]) {
      const select = find(key);
      options(select, versions.filter((item) => item.kind === type).map((item) => ({ value: item.id,
        label: `${item.name} · ${item.section_title}`, disabled: item.status !== "ready" || !item.quality_approved })), "Не применена");
      const configured = applied.find((item) => item.kind === type);
      select.value = configured?.version_id ?? "";
      select.className = configured ? "experience-active__select--applied" : "";
    }
  }
  async function reload() {
    const request = ++registryGeneration;
    const result = await api.versions();
    if (request !== registryGeneration) return;
    if (!Array.isArray(result.items) || !Array.isArray(result.applied)) throw new Error("Сервер вернул некорректный реестр версий.");
    versions = result.items; applied = result.applied;
    const previous = viewing;
    if (viewing) viewing = versions.find((item) => item.id === viewing.id) ?? null;
    paint();
    if (previous && !viewing) {
      generation += 1; selection.setMembers([]); await onViewed(null);
    }
  }
  async function view() {
    const request = ++generation;
    try {
      const record = choice.value ? await api.version(choice.value) : null;
      if (request !== generation) return;
      viewing = record; selection.setMembers(record?.members ?? []); paint();
      await onViewed(record);
      if (record) notice.textContent = `${record.name}: ${STATUS[record.status] ?? record.status}. В составе ${record.members.length} замечаний. ${record.error || ""}`;
    } catch (error) { if (request === generation) report(error); }
  }
  function open(rename = false) {
    editing = rename;
    find("version-title").textContent = rename ? "Переименовать версию" : kind.value === "vector" ? "Новая векторная база" : "Набор для дообучения";
    find("version-summary").textContent = rename ? viewing.name : `Выбрано ${selection.references().length} замечаний. Нужен один заполненный раздел нормативного документа.`;
    form.elements.namedItem("name").value = rename ? viewing.name : `Версия ${new Date().toLocaleString("ru")}`;
    form.elements.namedItem("model").value = rename ? viewing.model : model.value || DEFAULT_MODELS[kind.value];
    form.elements.namedItem("model").required = !rename;
    find("version-model-field").hidden = rename; find("version-error").textContent = "";
    dialog.showModal(); form.elements.namedItem("name").focus();
  }
  kind.addEventListener("change", () => { generation += 1; viewing = null; model.value = ""; choice.value = ""; paint(); void view(); });
  model.addEventListener("change", () => { viewing = null; choice.value = ""; paint(); void view(); });
  choice.addEventListener("change", view);
  find("build-version").addEventListener("click", () => { if (selection.references().length && !busy) open(); });
  find("version-rename").addEventListener("click", () => { if (viewing && !busy) open(true); });
  find("version-close").addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy) return; busy = true; save.disabled = true;
    try {
      const name = form.elements.namedItem("name").value;
      const result = editing ? await api.renameVersion(viewing.id, viewing.revision, name)
        : await api.createVersion({ kind: kind.value, name, model: form.elements.namedItem("model").value, items: selection.references() });
      dialog.close(); await reload(); model.value = result.model; paint(); choice.value = result.id; await view();
    } catch (error) { find("version-error").textContent = error.detail ?? error.message; }
    finally { busy = false; save.disabled = false; paint(); }
  });
  find("version-delete").addEventListener("click", async () => {
    if (!viewing || busy) return; busy = true; paint();
    try { await api.deleteVersion(viewing.id, viewing.revision); viewing = null; choice.value = ""; await reload(); await view(); }
    catch (error) { report(error); } finally { busy = false; paint(); }
  });
  for (const key of ["active-vector", "active-model"]) find(key).addEventListener("change", async () => {
    const select = find(key), chosen = versions.find((item) => item.id === select.value);
    if (!chosen || busy) { paint(); return; }
    busy = true; select.disabled = true;
    try { await api.applyVersion(chosen.id, chosen.revision); await reload(); find("active-status").textContent = `Применена версия «${chosen.name}» для раздела «${chosen.section_title}».`; }
    catch (error) { report(error); paint(); }
    finally { busy = false; select.disabled = false; }
  });
  return { reload };
}
