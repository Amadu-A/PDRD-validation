// frontend/src/js/features/help/controller.js

/** Локальный поиск, оглавление и информационный помощник без серверных запросов. */
import { GUIDE_SECTIONS, GUIDE_UPDATED, SCENARIOS, guideCardText, guideMatches } from "./content.js";
import { helpElement, renderGuideCard, scenarioCard } from "./view.js";

/** Подключает справку без чтения сессии и применения настроек к анализу. */
export function mountHelpCenter(root, { browser = globalThis.window } = {}) {
  const nav = root.querySelector("[data-help-toc]");
  const search = root.querySelector("[data-help-search]");
  const clear = root.querySelector("[data-help-clear]");
  const status = root.querySelector("[data-help-status]");
  const articles = root.querySelector("[data-help-articles]");
  root.querySelector("[data-help-updated]").textContent = `Обновлено: ${GUIDE_UPDATED}`;
  const links = [], sections = [];
  for (const definition of GUIDE_SECTIONS) {
    const link = helpElement("a", definition.title);
    link.href = `#${definition.id}`;
    link.dataset.helpSection = definition.id;
    link.addEventListener("click", () => { search.value = ""; filter(); });
    nav.append(link); links.push(link);
    const section = helpElement("section", null, "help-center__section");
    section.id = definition.id;
    section.tabIndex = -1;
    const heading = helpElement("h2", definition.title);
    heading.id = `${definition.id}-title`;
    heading.tabIndex = -1;
    section.setAttribute("aria-labelledby", heading.id);
    const content = helpElement("div", null, "help-center__cards");
    section.append(heading, helpElement("p", definition.intro), content);
    articles.append(section);
    sections.push({ definition, section, content });
  }

  const selector = helpElement("select");
  selector.id = "helpScenario";
  for (const scenario of SCENARIOS) {
    const option = helpElement("option", scenario.title);
    option.value = scenario.id;
    selector.append(option);
  }
  selector.value = SCENARIOS[0].id;
  const label = helpElement("label", "Что вы хотите проверить?", "help-center__scenario-label");
  label.setAttribute("for", selector.id);
  const selectorBox = helpElement("div", null, "help-center__scenario-choice");
  selectorBox.append(label, selector);
  const scenarioOutput = helpElement("div");
  scenarioOutput.setAttribute("aria-live", "polite");

  function paintScenarios(query) {
    /** Смена сценария обновляет только инструкцию, сохраняя фокус выбора. */
    scenarioOutput.replaceChildren();
    const candidates = query ? SCENARIOS.filter((scenario) => guideMatches(Object.values(scenario).join(" "), query))
      : SCENARIOS.filter((scenario) => scenario.id === selector.value);
    for (const scenario of candidates) scenarioOutput.append(scenarioCard(scenario));
    selectorBox.hidden = Boolean(query);
    return candidates.length;
  }

  function filter() {
    /** Фильтрует карточки по всем словам запроса, не меняя параметры анализа. */
    const query = search.value.trim().slice(0, 200);
    let count = 0;
    for (const { definition, section, content } of sections) {
      content.replaceChildren();
      let matches = 0;
      const sectionMatches = Boolean(query) && guideMatches(`${definition.title} ${definition.intro}`, query);
      if (definition.id === "scenarios") {
        matches = paintScenarios(sectionMatches ? "" : query);
        content.append(selectorBox, scenarioOutput);
      } else {
        for (const item of definition.cards) {
          if (!query || sectionMatches || guideMatches(guideCardText(item), query)) {
            content.append(renderGuideCard(item, { expanded: Boolean(query) }));
            matches += 1;
          }
        }
      }
      section.hidden = matches === 0;
      count += matches;
    }
    clear.hidden = !query;
    status.textContent = query ? (count ? `Найдено карточек: ${count}` : "Ничего не найдено. Попробуйте «оборудование», «ТЗ» или «галочка».") : "Все разделы руководства";
  }

  function activate({ focus = false } = {}) {
    /** Поддерживает прямые ссылки, возврат назад и раскрытие ответа FAQ. */
    const id = browser?.location.hash.slice(1) || "quick-start";
    const activeSection = sections.find(({ definition }) => definition.id === id || definition.cards.some((item) => item.id === id))?.definition.id;
    for (const link of links) {
      if (link.dataset.helpSection === (activeSection || "quick-start")) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
    const target = root.querySelector(`[id="${/^[a-z0-9-]+$/.test(id) ? id : "quick-start"}"]`);
    if (target?.tagName === "DETAILS") target.open = true;
    if (focus) target?.focus({ preventScroll: true });
    if (browser?.location.hash) target?.scrollIntoView?.({ block: "start" });
  }

  const hashChange = () => { search.value = ""; filter(); activate({ focus: true }); };
  const clearSearch = () => { search.value = ""; filter(); search.focus(); };
  search.addEventListener("input", filter);
  clear.addEventListener("click", clearSearch);
  selector.addEventListener("change", () => paintScenarios(""));
  browser?.addEventListener("hashchange", hashChange);
  filter(); activate();
  return { selector, filter, dispose() {
    search.removeEventListener("input", filter);
    clear.removeEventListener("click", clearSearch);
    browser?.removeEventListener("hashchange", hashChange);
  } };
}
