// frontend/tests/portal-navigation.test.js

/** Функциональная проверка переходов между разделами кабинета и админки. */
import assert from "node:assert/strict";
import test from "node:test";
import { bindPortalNavigation } from "../src/js/features/portal/navigation.js";
import { FakeElement } from "./helpers/fake-dom.js";

test("навигация по hash открывает ровно выбранный раздел", () => {
  const nav = new FakeElement();
  const links = ["overview", "users", "roles"].map((id) => {
    const link = new FakeElement("a");
    link.setAttribute("href", `#${id}`);
    return link;
  });
  nav.lists.set('a[href^="#"]', links);
  const sections = ["overview", "users", "roles"].map((id) => {
    const section = new FakeElement("section");
    section.dataset.adminSection = id;
    return section;
  });
  let hashChange;
  globalThis.document = {
    querySelectorAll: () => sections,
  };
  globalThis.window = {
    location: { hash: "#users" },
    addEventListener: (_name, listener) => { hashChange = listener; },
  };
  bindPortalNavigation(nav, { showSections: true });
  assert.equal(sections[1].hidden, false);
  assert.equal(sections[0].hidden, true);
  assert.equal(links[1].getAttribute("aria-current"), "location");
  window.location.hash = "#roles";
  hashChange();
  assert.equal(sections[2].hidden, false);
  assert.equal(sections[1].hidden, true);
  assert.equal(links[2].getAttribute("aria-current"), "location");
});
