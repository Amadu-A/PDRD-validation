// frontend/src/js/features/experience/selection.js

/** Выбор редакций между страницами, атомарное удаление и поиск отсутствующих в версии. */
export function createExperienceSelection({ api, criteria, onChanged, onSaved, notice, document: dom = document }) {
  const find = (key) => dom.querySelector(`[data-experience-${key}]`);
  const selected = new Map();
  let members = new Map();
  let busy = false;
  let generation = 0;
  const report = (error) => { notice.textContent = error.detail ?? error.message; };
  const references = () => [...selected].map(([id, revision]) => ({ id, revision }));
  function changed() {
    find("selected-count").textContent = `Выбрано: ${selected.size}`;
    find("delete-selection").disabled = busy || !selected.size;
    find("build-version").disabled = busy || !selected.size;
    find("select-all").disabled = busy;
    find("select-missing").disabled = busy || !members.size;
    onChanged();
  }
  function toggle(example, checked) {
    if (checked && selected.size >= 1000 && !selected.has(example.id)) {
      notice.textContent = "За один раз можно выбрать до 1000 замечаний."; return;
    }
    if (checked) selected.set(example.id, example.revision); else selected.delete(example.id);
    changed();
  }
  function clear() { generation += 1; selected.clear(); changed(); }
  async function scan(missing = false) {
    if (busy) return;
    const request = ++generation; const filters = criteria();
    busy = true; changed();
    try {
      const found = new Map();
      for (let offset = 0; ; offset += 100) {
        const result = await api.list({ ...filters, offset, limit: 100 });
        if (request !== generation) return;
        if (!Array.isArray(result.items) || !Number.isSafeInteger(result.total)) throw new Error("Некорректный ответ каталога.");
        for (const example of result.items) {
          if (!missing || members.get(example.id) !== example.revision) found.set(example.id, example.revision);
        }
        if (found.size > 1000) throw new Error("Сузьте фильтры: выбрано больше 1000 замечаний.");
        if (offset + result.items.length >= result.total) break;
        if (!result.items.length) throw new Error("Каталог изменился; повторите выбор.");
      }
      if (request === generation) { selected.clear(); for (const [id, revision] of found) selected.set(id, revision); }
    } catch (error) { report(error); }
    finally { busy = false; changed(); }
  }
  async function remove(items) {
    if (busy || !items.length) return;
    busy = true; changed();
    try { await api.deleteSelection(items); clear(); await onSaved(); notice.textContent = `Удалено замечаний: ${items.length}. История сохранена.`; }
    catch (error) { report(error); }
    finally { busy = false; changed(); }
  }
  find("delete-selection").addEventListener("click", () => remove(references()));
  find("select-all").addEventListener("click", () => scan());
  find("select-missing").addEventListener("click", () => scan(true));
  find("clear-selection").addEventListener("click", clear);
  return { selected, references, toggle, clear, changed,
    remove: (example) => remove([{ id: example.id, revision: example.revision }]),
    memberRevision: (id) => members.get(id) ?? null,
    setMembers(items) { members = new Map(items.map((item) => [item.example_id, item.example_revision])); clear(); },
  };
}
