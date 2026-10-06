// frontend/tests/admin-directory.test.js

/** Админский справочник и CAS назначение руководителя через членство отдела. */
import assert from "node:assert/strict";
import test from "node:test";

import { setCsrfToken } from "../src/js/features/auth/api.js";
import { allDepartments, allOrganizations } from "../src/js/features/admin/directory.js";
import { bindDirectoryPage } from "../src/js/features/admin/directory-page.js";
import { createRoleForm } from "../src/js/features/admin/role-form.js";
import { saveRoleWithMembership } from "../src/js/features/admin/role-assignment.js";
import { FakeElement } from "./helpers/fake-dom.js";

const USER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ORG = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const DEPT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

test("справочник читает все страницы организации и отдела", async () => {
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    const offset = Number(new URL(url, "http://local").searchParams.get("offset"));
    const isDepartment = url.includes("/departments");
    return new Response(JSON.stringify({ items: [isDepartment
      ? { department_id: offset ? DEPT : USER, name: `Отдел ${offset}`, active: true }
      : { organization_id: offset ? ORG : USER, name: `Организация ${offset}`, active: true }],
    total: 2, limit: 100, offset }), { status: 200 });
  };
  try {
    assert.equal((await allOrganizations()).length, 2);
    assert.equal((await allDepartments(ORG)).length, 2);
    assert.deepEqual(calls.map((url) => new URL(url, "http://local").searchParams.get("offset")),
      ["0", "1", "0", "1"]);
  } finally { globalThis.fetch = previousFetch; }
});

test("руководителю сначала создаётся членство, затем роль с новой CAS версией", async () => {
  const calls = [];
  const scope = { kind: "department", organization_id: ORG, department_id: DEPT };
  const user = { user_id: USER, authorization_version: 3 };
  await saveRoleWithMembership({ user, role: "department_head", scope, memberships: [] }, {
    activate: async (...args) => {
      calls.push(["membership", ...args]);
      return { authorization_version: 4 };
    },
    changeRole: async (...args) => { calls.push(["role", ...args]); return {}; },
  });
  assert.deepEqual(calls, [
    ["membership", USER, ORG, DEPT, 3],
    ["role", USER, "department_head", scope, 4],
  ]);
});

test("действующее членство не записывается повторно, частичный сбой объясняется", async () => {
  const scope = { kind: "department", organization_id: ORG, department_id: DEPT };
  const user = { user_id: USER, authorization_version: 7 };
  let activated = 0;
  await saveRoleWithMembership({ user, role: "department_head", scope,
    memberships: [{ organization_id: ORG, department_id: DEPT, active: true }] }, {
    activate: async () => { activated += 1; },
    changeRole: async (_user, _role, _scope, version) => { assert.equal(version, 7); },
  });
  assert.equal(activated, 0);
  await assert.rejects(() => saveRoleWithMembership({ user, role: "department_head", scope,
    memberships: [] }, {
    activate: async () => ({ authorization_version: 8 }),
    changeRole: async () => { throw new Error("Конфликт версии"); },
  }), (error) => error.partial && /Членство создано.*роль не изменена/.test(error.message));
});


/** Подготавливает настоящие контракты каталога разделов и текущих назначений. */
function setup(t, status = 200) {
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/section-catalog")) return new Response(JSON.stringify([
      { section_id: ORG, name: "Электроснабжение" }, { section_id: DEPT, name: "Вентиляция" },
    ]));
    if (url.endsWith("/section-access")) return new Response(JSON.stringify({ section_ids: [ORG], authorization_version: 2 }));
    return new Response(JSON.stringify(status === 409 ? { detail: "Версия изменилась" } : {}), { status });
  };
  setCsrfToken("csrf-token");
  t.after(() => { globalThis.document = previousDocument; globalThis.fetch = previousFetch; setCsrfToken(""); });
  return calls;
}

test("роль и несколько разделов передаются одним PATCH с CSRF и CAS", async (t) => {
  const calls = setup(t);
  let changed = 0;
  const form = createRoleForm({ user_id: USER, authorization_version: 2, status: "active", currentRole: "designer" }, async () => { changed += 1; });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const role = form.children[0].children[0];
  const choices = form.children[1].children[1];
  assert.equal(choices.children[0].children[0].checked, true);
  assert.equal(choices.children[1].children[0].checked, false);
  choices.children[1].children[0].checked = true;
  role.value = "department_head";
  role.dispatch("change");
  await form.listeners.get("submit")[0]({ preventDefault() {} });
  const writes = calls.filter(({ options }) => options.method !== "GET");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].options.method, "PATCH");
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "csrf-token");
  assert.deepEqual(JSON.parse(writes[0].options.body), { role: "department_head", scope: { kind: "sections" }, authorization_version: 2, section_ids: [ORG, DEPT] });
  assert.equal(changed, 1);
});

test("администратор назначается без отдела, CAS конфликт блокирует повтор", async (t) => {
  const calls = setup(t, 409);
  const form = createRoleForm({ user_id: USER, authorization_version: 2, status: "active", currentRole: "" }, async () => {});
  await new Promise((resolve) => setTimeout(resolve, 0));
  const role = form.children[0].children[0];
  role.value = "platform_admin";
  role.dispatch("change");
  assert.equal(form.children[3].disabled, false);
  await form.listeners.get("submit")[0]({ preventDefault() {} });
  assert.equal(form.children[3].disabled, true);
  await form.listeners.get("submit")[0]({ preventDefault() {} });
  assert.equal(calls.filter(({ options }) => options.method === "PATCH").length, 1);
  assert.equal(JSON.parse(calls.at(-1).options.body).scope.kind, "platform");
});

test("справочник читает каталог главной страницы без создания отделов", async (t) => {
  const calls = setup(t);
  const root = new FakeElement();
  const list = new FakeElement("ul");
  root.selectors.set("[data-directory-departments]", list);
  root.selectors.set("[data-directory-status]", new FakeElement("p"));
  await bindDirectoryPage(root).load();
  assert.deepEqual(list.children.map((item) => item.textContent), ["Электроснабжение", "Вентиляция"]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "GET");
});
