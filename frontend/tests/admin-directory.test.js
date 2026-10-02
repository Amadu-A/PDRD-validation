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

test("форма руководителя выбирает названия и вызывает membership перед PATCH роли", async () => {
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const calls = [];
  let changed = 0;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.includes("/organizations?") && options.method === "GET") {
      return new Response(JSON.stringify({ items: [{ organization_id: ORG, name: "Нео Терм", active: true }], total: 1 }), { status: 200 });
    }
    if (url.endsWith(`/users/${USER}/memberships`) && options.method === "GET") {
      return new Response("[]", { status: 200 });
    }
    if (url.includes("/departments?") && options.method === "GET") {
      return new Response(JSON.stringify({ items: [{ department_id: DEPT, name: "Проектный", active: true }], total: 1 }), { status: 200 });
    }
    if (options.method === "PUT") {
      return new Response(JSON.stringify({ authorization_version: 3 }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  };
  setCsrfToken("csrf-token");
  try {
    const form = createRoleForm({ user_id: USER, authorization_version: 2,
      tier: "member", currentRole: "designer", roles: ["designer"] },
    async () => { changed += 1; });
    const role = form.children[0].children[0];
    const fields = form.children[1];
    const organization = fields.children[0].children[0];
    const department = fields.children[1].children[0];
    role.value = "department_head";
    role.dispatch("change");
    await new Promise((resolve) => setTimeout(resolve, 0));
    organization.value = ORG;
    organization.dispatch("change");
    await new Promise((resolve) => setTimeout(resolve, 0));
    department.value = DEPT;
    department.dispatch("change");
    await form.listeners.get("submit")[0]({ preventDefault() {} });
    assert.equal(changed, 1);
    assert.deepEqual(calls.filter(({ options }) => ["PUT", "PATCH"].includes(options.method))
      .map(({ options }) => options.method), ["PUT", "PATCH"]);
    const patch = calls.find(({ options }) => options.method === "PATCH");
    assert.equal(JSON.parse(patch.options.body).authorization_version, 3);
    assert.equal(patch.options.headers["X-CSRF-Token"], "csrf-token");
  } finally {
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
    setCsrfToken("");
  }
});

test("админка создаёт организацию и отдел через CSRF API", async () => {
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  const select = new FakeElement("select");
  const organizationForm = new FakeElement("form");
  const departmentForm = new FakeElement("form");
  const list = new FakeElement("ul");
  const status = new FakeElement("p");
  const organizationButton = new FakeElement("button");
  const departmentButton = new FakeElement("button");
  const organizationName = { value: "Нео Терм" };
  const departmentName = { value: "Проектный" };
  organizationForm.elements = { namedItem: () => organizationName };
  departmentForm.elements = { namedItem: () => departmentName };
  organizationForm.selectors.set('button[type="submit"]', organizationButton);
  departmentForm.selectors.set('button[type="submit"]', departmentButton);
  for (const [key, value] of [
    ["[data-directory-organization]", select], ["[data-create-organization]", organizationForm],
    ["[data-create-department]", departmentForm], ["[data-directory-departments]", list],
    ["[data-directory-status]", status],
  ]) root.selectors.set(key, value);
  const organizations = [];
  const departments = [];
  const methods = [];
  globalThis.fetch = async (url, options) => {
    methods.push(options.method);
    if (url.includes("/organizations?") && options.method === "GET") {
      return new Response(JSON.stringify({ items: organizations, total: organizations.length }), { status: 200 });
    }
    if (url.endsWith("/organizations") && options.method === "POST") {
      organizations.push({ organization_id: ORG, name: JSON.parse(options.body).name, active: true });
      return new Response(JSON.stringify(organizations[0]), { status: 201 });
    }
    if (url.includes("/departments?") && options.method === "GET") {
      return new Response(JSON.stringify({ items: departments, total: departments.length }), { status: 200 });
    }
    if (url.endsWith("/departments") && options.method === "POST") {
      departments.push({ department_id: DEPT, organization_id: ORG,
        name: JSON.parse(options.body).name, active: true });
      return new Response(JSON.stringify(departments[0]), { status: 201 });
    }
    throw new Error(`Неожиданный путь ${url}`);
  };
  setCsrfToken("csrf-token");
  try {
    const directory = bindDirectoryPage(root);
    await directory.load();
    await organizationForm.listeners.get("submit")[0]({ preventDefault() {} });
    assert.equal(select.value, ORG);
    await departmentForm.listeners.get("submit")[0]({ preventDefault() {} });
    assert.match(list.children[0].textContent, /Проектный/);
    assert.deepEqual(methods.filter((method) => method === "POST"), ["POST", "POST"]);
  } finally {
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
    setCsrfToken("");
  }
});
