// frontend/src/js/features/experience/versions.js

/**
 * Реестр: просмотр и подсветка отделены от ручной подготовки наборов и применения.
 * API сохраняет фиксированный состав; UI объясняет серверный допуск качества.
 */
import { element } from "./rows.js";
import { experienceVersionStatus } from "./version-status.js";

const DEFAULT_MODELS = { vector: "shared-embedding", fine_tune: "shared-vlm" };

/** Подключает события реестра, сохраняя выбор строк при смене просматриваемой версии. */
export function mountExperienceVersions({ api, selection, onViewed, notice, document: dom = document }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const kind = find("version-kind"), model = find("version-model"), choice = find("version");
  const dialog = find("version-dialog"), form = find("version-form"), save = find("version-save");
  let versions = [], applied = [], viewing = null, editing = false, creationKind = "vector", busy = false, generation = 0, registryGeneration = 0;
  const report = (error) => { notice.textContent = error.detail ?? error.message; };
  /** Сохраняет блокировку подготовки при уведомлениях выбора во время записи версии. */
  function setBusy(value) { busy = value; selection.setPreparationBusy(value); }
  /** Перестраивает безопасные option-узлы, сохраняя доступное значение выбора. */
  function options(select, items, placeholder) {
    const current = select.value;
    const first = element("option", "", placeholder); first.value = "";
    select.replaceChildren(first, ...items.map(({ value, label, disabled = false }) => {
      const option = element("option", "", label); option.value = value; option.disabled = disabled; return option;
    }));
    select.value = items.some((item) => item.value === current) ? current : "";
  }
  /** Отображает реестр и причины запретов без изменения состава выбранных строк. */
  function paint() {
    const models = [...new Set([DEFAULT_MODELS[kind.value], ...versions.filter((item) => item.kind === kind.value).map((item) => item.model)])];
    options(model, models.map((value) => ({ value, label: value })), "Все модели");
    const visible = versions.filter((item) => item.kind === kind.value && (!model.value || item.model === model.value));
    options(choice, visible.map((item) => ({ value: item.id, label: `${item.name} · ${experienceVersionStatus(item).label} · ${new Date(item.created_at).toLocaleDateString("ru")}` })), "Все замечания");
    find("version-rename").disabled = !viewing || busy;
    find("version-delete").disabled = !viewing || busy;
    for (const key of ["build-version", "prepare-fine-tune"]) find(key).disabled = busy || selection.isBusy() || !selection.references().length;
    find("version-status").hidden = !viewing;
    if (viewing) {
      const status = experienceVersionStatus(viewing);
      find("version-status-title").textContent = `Просмотр: ${viewing.name}`;
      find("version-build-status").textContent = `${status.build} В составе ${viewing.members.length} замечаний.`;
      find("version-quality-status").textContent = status.quality;
      find("version-apply-status").textContent = status.application;
      find("version-failure-status").textContent = viewing.error || "";
      find("version-failure-status").hidden = !viewing.error;
    }
    for (const [type, key] of [["vector", "active-vector"], ["fine_tune", "active-model"]]) {
      const select = find(key);
      options(select, versions.filter((item) => item.kind === type).map((item) => ({ value: item.id,
        label: `${item.name} · ${item.section_title} · ${experienceVersionStatus(item).canApply ? "проверена" : "применение недоступно"}`,
        disabled: !experienceVersionStatus(item).canApply })), "Не применена");
      const configured = applied.find((item) => item.kind === type);
      select.value = configured?.version_id ?? "";
      select.className = configured ? "experience-active__select--applied" : "";
    }
  }
  /** Обновляет серверный реестр; устаревший ответ не заменяет более новый. */
  async function reload() {
    const request = ++registryGeneration;
    const result = await api.versions();
    if (request !== registryGeneration) return;
    if (!Array.isArray(result.items) || !Array.isArray(result.applied)) throw new Error("Сервер вернул некорректный реестр версий.");
    versions = result.items; applied = result.applied;
    const previous = viewing;
    if (viewing) {
      const latest = versions.find((item) => item.id === viewing.id);
      // Реестр возвращает member_count вместо members; неизменяемый состав уже загружен для просмотра.
      viewing = latest ? { ...viewing, ...latest, members: viewing.members } : null;
    }
    paint();
    if (previous && !viewing) {
      generation += 1; selection.setMembers([]); await onViewed(null);
    }
  }
  /** Загружает состав для подсветки, сохраняя ручной выбор и рабочие назначения. */
  async function view() {
    const request = ++generation;
    try {
      const record = choice.value ? await api.version(choice.value) : null;
      if (request !== generation) return;
      viewing = record; selection.setMembers(record?.members ?? []); paint();
      await onViewed(record);
      if (record) notice.textContent = `${record.name}: ${experienceVersionStatus(record).label}. В составе ${record.members.length} замечаний. ${record.error || ""}`;
    } catch (error) { if (request === generation) report(error); }
  }
  /** Открывает диалог отдельного действия: его тип не зависит от режима просмотра. */
  function open(rename = false, type = kind.value) {
    editing = rename;
    creationKind = type;
    find("version-title").textContent = rename ? "Переименовать версию" : type === "vector" ? "Новая векторная база" : "Подготовить набор для дообучения VLM";
    const purpose = type === "vector" ? "Будет создана отдельная коллекция Qdrant; эмбеддинговая модель не переобучается." : "Будет сохранён фиксированный набор примеров. Фактическое обучение и новые веса пока не создаются.";
    find("version-summary").textContent = rename ? viewing.name : `Выбрано ${selection.references().length} замечаний. Раздел берётся из нормативной базы; сервер проверит пригодность примеров. ${purpose}`;
    form.elements.namedItem("name").value = rename ? viewing.name : `Версия ${new Date().toLocaleString("ru")}`;
    form.elements.namedItem("model").value = rename ? viewing.model : (type === kind.value && model.value) || DEFAULT_MODELS[type];
    form.elements.namedItem("model").required = !rename;
    find("version-model-field").hidden = rename; find("version-error").textContent = "";
    dialog.showModal(); form.elements.namedItem("name").focus();
  }
  kind.addEventListener("change", () => { generation += 1; viewing = null; model.value = ""; choice.value = ""; paint(); void view(); });
  model.addEventListener("change", () => { viewing = null; choice.value = ""; paint(); void view(); });
  choice.addEventListener("change", view);
  for (const [key, type] of [["build-version", "vector"], ["prepare-fine-tune", "fine_tune"]]) {
    find(key).addEventListener("click", () => { if (selection.references().length && !busy && !selection.isBusy()) open(false, type); });
  }
  find("version-rename").addEventListener("click", () => { if (viewing && !busy) open(true); });
  find("version-close").addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy) return; setBusy(true); save.disabled = true; paint();
    try {
      const name = form.elements.namedItem("name").value;
      const result = editing ? await api.renameVersion(viewing.id, viewing.revision, name)
        : await api.createVersion({ kind: creationKind, name, model: form.elements.namedItem("model").value, items: selection.references() });
      dialog.close(); kind.value = result.kind; await reload(); model.value = result.model; paint(); choice.value = result.id; await view();
      selection.refreshRevisions(result.repaired ?? []);
      if (result.excluded?.length) notice.textContent = `В версию включено: ${result.members.length}. Исключено: ${result.excluded.length}. `
        + result.excluded.map((item) => `${item.id}: ${item.reason}`).join("; ");
    } catch (error) { find("version-error").textContent = error.detail ?? error.message; }
    finally { setBusy(false); save.disabled = false; paint(); }
  });
  find("version-delete").addEventListener("click", async () => {
    if (!viewing || busy) return; setBusy(true); paint();
    try { await api.deleteVersion(viewing.id, viewing.revision); viewing = null; choice.value = ""; await reload(); await view(); }
    catch (error) { report(error); } finally { setBusy(false); paint(); }
  });
  for (const key of ["active-vector", "active-model"]) find(key).addEventListener("change", async () => {
    const select = find(key), chosen = versions.find((item) => item.id === select.value);
    if (!chosen || busy || !experienceVersionStatus(chosen).canApply) { paint(); return; }
    setBusy(true); select.disabled = true;
    try { await api.applyVersion(chosen.id, chosen.revision); await reload(); find("active-status").textContent = `Назначение версии «${chosen.name}» для раздела «${chosen.section_title}» сохранено в реестре. Подключение к рабочему анализу ещё не реализовано.`; }
    catch (error) { report(error); paint(); }
    finally { setBusy(false); select.disabled = false; }
  });
  return { reload };
}
