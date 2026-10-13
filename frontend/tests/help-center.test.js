// frontend/tests/help-center.test.js

/** Справка гостю, локальный поиск, ссылки, сценарии и единый текст документации. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { mountHelpCenter } from "../src/js/features/help/controller.js";
import { GUIDE_SECTIONS, SCENARIOS, SOURCES, guideMatches } from "../src/js/features/help/content.js";
import { renderGuideMarkdown } from "../src/js/features/help/markdown.js";
import { mountAnalysisHints } from "../src/js/features/help/context-hints.js";
import { mountHelpNavigation } from "../src/js/features/help/navigation.js";
import { renderGuideCard } from "../src/js/features/help/view.js";

/** Обходит дерево без ограничений тестового querySelector. */
function collect(node) { return [node, ...node.children.flatMap(collect)]; }

/** Подготавливает страницу без авторизации и запрещает любой сетевой запрос. */
function setup(t, hash = "") {
  const previous = { document: globalThis.document, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.fetch = () => { throw new Error("Справка не должна обращаться к API"); };
  const root = new FakeElement();
  for (const name of ["toc", "search", "clear", "status", "articles", "updated"]) {
    root.selectors.set(`[data-help-${name}]`, new FakeElement(name === "search" ? "input" : "div"));
  }
  const querySelector = root.querySelector.bind(root);
  root.querySelector = (selector) => {
    const id = /^\[id="([^"]+)"\]$/.exec(selector)?.[1];
    return id ? collect(root.selectors.get("[data-help-articles]")).find((node) => node.id === id) : querySelector(selector);
  };
  const listeners = new Map();
  const browser = { location: { hash }, addEventListener: (name, handler) => listeners.set(name, handler), removeEventListener: (name) => listeners.delete(name) };
  const controller = mountHelpCenter(root, { browser });
  return { root, browser, listeners, controller, search: root.querySelector("[data-help-search]") };
}

test("гость читает все разделы без сессии и API; прямая ссылка раскрывает FAQ", (t) => {
  const { root, controller } = setup(t, "#faq-unverified");
  const articles = root.querySelector("[data-help-articles]");
  assert.equal(articles.children.length, GUIDE_SECTIONS.length);
  assert.ok(articles.children.every((section) => !section.hidden));
  assert.equal(root.querySelector('[id="faq-unverified"]').open, true);
  assert.equal(root.querySelector("[data-help-toc]").children.find((link) => link.dataset.helpSection === "faq").getAttribute("aria-current"), "location");
  controller.dispose();
});

test("поиск находит пояснения и таблицы, раскрывает ответы; сброс возвращает всё", (t) => {
  const { root, search, controller } = setup(t);
  search.value = "неподтвержденные источники";
  search.dispatch("input");
  assert.match(root.querySelector("[data-help-status]").textContent, /Найдено/);
  assert.equal(root.querySelector('[id="unverified-sources"]').hidden, false);
  search.value = "XYZ-not-found"; search.dispatch("input");
  assert.match(root.querySelector("[data-help-status]").textContent, /Ничего/);
  assert.ok(root.querySelector("[data-help-articles]").children.every((section) => section.hidden));
  root.querySelector("[data-help-clear]").click();
  assert.equal(search.value, ""); assert.equal(search.focused, true);
  assert.ok(root.querySelector("[data-help-articles]").children.every((section) => !section.hidden));
  controller.dispose();
});

test("помощник показывает все шесть сценариев без запуска и применения параметров", (t) => {
  const { root, controller } = setup(t);
  for (const scenario of SCENARIOS) {
    controller.selector.value = scenario.id; controller.selector.dispatch("change");
    const nodes = collect(root.querySelector('[id="scenarios"]'));
    assert.ok(nodes.some((node) => node.textContent === scenario.title));
    assert.ok(nodes.some((node) => node.textContent === scenario.outside));
    assert.equal(nodes.filter((node) => node.tagName === "BUTTON" || node.tagName === "FORM").length, 0);
  }
  controller.dispose();
});

test("навигация после поиска и переход назад восстанавливают целевой раздел", (t) => {
  const { root, browser, listeners, search, controller } = setup(t);
  search.value = "XYZ-not-found"; search.dispatch("input");
  browser.location.hash = "#document-context"; listeners.get("hashchange")();
  assert.equal(search.value, "");
  assert.equal(root.querySelector('[id="document-context"]').focused, true);
  assert.equal(root.querySelector('[id="settings"]').hidden, false);
  controller.dispose(); assert.equal(listeners.has("hashchange"), false);
});

test("ввод HTML и символов regex остаётся безопасным текстом", (t) => {
  const { search, controller } = setup(t);
  search.value = '<img src=x onerror=alert(1)> [.*';
  assert.doesNotThrow(() => search.dispatch("input"));
  const card = renderGuideCard({ id: "example", title: "Текст", paragraphs: [search.value] });
  assert.equal(card.children[1].textContent, search.value);
  assert.equal(collect(card).filter((node) => node.tagName === "IMG").length, 0);
  assert.equal(guideMatches("Неподтверждённые ИСТОЧНИКИ", "источники неподтвержденные"), true);
  controller.dispose();
});

test("подсказки берут текст из справки, не меняют форму и не дублируются", (t) => {
  setup(t);
  const root = new FakeElement(), settings = GUIDE_SECTIONS.find((section) => section.id === "settings");
  const input = Object.assign(new FakeElement("input"), { checked: true, disabled: false });
  root.append(input);
  for (const card of settings.cards) root.selectors.set(`[data-setting-help="${card.id}"]`, new FakeElement());
  mountAnalysisHints(root); mountAnalysisHints(root);
  for (const card of settings.cards) {
    const host = root.querySelector(`[data-setting-help="${card.id}"]`);
    assert.equal(host.children.length, 1);
    const nodes = collect(host);
    assert.ok(nodes.some((node) => node.textContent === card.paragraphs[0]));
    assert.ok(nodes.some((node) => node.href === `/help.html#${card.id}`));
    assert.ok(nodes.some((node) => node.tagName === "SUMMARY" && node.getAttribute("aria-label")));
  }
  assert.equal(input.checked, true); assert.equal(input.disabled, false);
});

test("общая навигация добавляет публичную справку один раз", (t) => {
  setup(t);
  const root = new FakeElement(), nav = new FakeElement("nav");
  root.selectors.set('nav[aria-label="Основная навигация"]', nav);
  mountHelpNavigation(root); mountHelpNavigation(root);
  assert.equal(nav.children.length, 1);
  assert.equal(nav.children[0].href, "/help.html");
});

test("Markdown совпадает с единым текстом; D, EQ, U и E не перепутаны", () => {
  const markdown = renderGuideMarkdown();
  assert.equal(readFileSync(new URL("../../docs/user-guide.md", import.meta.url), "utf8").replaceAll("\r\n", "\n"), markdown);
  assert.equal(SOURCES.find((source) => source.id === "D").title, "Межстраничные сведения");
  assert.equal(SOURCES.find((source) => source.id === "EQ").title, "Документация производителя");
  assert.equal(SOURCES.find((source) => source.id === "U").title, "Дополнительные документы");
  assert.match(SOURCES.find((source) => source.id === "E").text, /выключен/);
  assert.match(markdown, /не гарантируют полноту/);
  const ids = GUIDE_SECTIONS.flatMap((section) => [section.id, ...section.cards.map((card) => card.id)]);
  assert.equal(new Set(ids).size, ids.length);
});

test("публичная страница, модули и CSS imports доступны в статическом frontend", () => {
  const page = readFileSync(new URL("../src/help.html", import.meta.url), "utf8");
  assert.match(page, /lang="ru"/);
  assert.match(page, /type="module" src="\/js\/features\/help\/page.js"/);
  assert.match(page, /href="\/css\/style.css"/);
  assert.doesNotMatch(page, /on(?:click|change|input)=|<style|type="checkbox"/);
  assert.match(page, /for="helpSearch"/);
  const style = readFileSync(new URL("../src/css/style.css", import.meta.url), "utf8");
  for (const match of style.matchAll(/@import url\("([^\"]+)"\)/g)) {
    assert.ok(readFileSync(new URL(`../src/css/${match[1]}`, import.meta.url), "utf8").length > 0);
  }
  for (const name of ["page", "controller", "content", "settings", "view", "context-hints", "navigation"]) {
    const source = readFileSync(new URL(`../src/js/features/help/${name}.js`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|eval\(/);
    for (const match of source.matchAll(/from "(\.[^\"]+)"/g)) {
      assert.ok(readFileSync(new URL(`../src/js/features/help/${match[1]}`, import.meta.url), "utf8").length > 0);
    }
  }
});
