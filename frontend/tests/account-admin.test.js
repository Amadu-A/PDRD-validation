// frontend/tests/account-admin.test.js

/** Проверяет единые права кабинета и безопасный контракт редактора ролей. */
import assert from "node:assert/strict";
import test from "node:test";
import { accessDescription, hasPermission, isAdmin } from "../src/js/features/auth/access.js";
import { partitionCapabilities } from "../src/js/features/account/capabilities.js";
import { roleScope } from "../src/js/features/admin/role-form.js";
import { filterUsers, roleEditorState } from "../src/js/features/admin/users.js";
import { listUsers } from "../src/js/features/admin/api.js";
import { bindMainAccess } from "../src/js/features/auth/main-access.js";
import { clearSession, refreshSession } from "../src/js/features/auth/session.js";
import { FakeElement } from "./helpers/fake-dom.js";
import { consumeVerificationToken } from "../src/js/features/account/verification-link.js";

test("гость и бесплатный пользователь не получают административные действия", () => {
  const guest = { authenticated: false, user: null };
  const free = { authenticated: true, user: { roles: [], permissions: ["analysis.run"] } };
  assert.equal(accessDescription(guest), "Гость");
  assert.equal(accessDescription(free), "Бесплатный аккаунт");
  assert.equal(isAdmin(free), false);
  assert.equal(hasPermission(guest, "admin.access"), false);
  assert.ok(partitionCapabilities(guest).locked.includes("Администрирование PDRD"));
});

test("проектировщик видит Gold, но не утверждение Review", () => {
  const state = { authenticated: true, user: {
    roles: ["designer"], permissions: ["analysis.run", "review.gold.create",
      "user_documents.own.write"],
  } };
  assert.equal(accessDescription(state), "Проектировщик");
  const result = partitionCapabilities(state);
  assert.ok(result.allowed.includes("Создание Gold-замечаний"));
  assert.ok(result.locked.includes("Утверждение Human Review"));
  assert.ok(result.locked.includes("Собственные пользовательские документы (временно недоступны)"));
});

test("роль руководителя требует точную область, дизайнер ограничен своим", () => {
  assert.deepEqual(roleScope("designer"), { kind: "own" });
  assert.deepEqual(roleScope("platform_admin"), { kind: "platform" });
  assert.throws(() => roleScope("department_head"), /организацию и отдел/);
  const organizationId = "11111111-1111-1111-1111-111111111111";
  const departmentId = "22222222-2222-2222-2222-222222222222";
  assert.throws(() => roleScope("department_head", "org", "dept"), /организацию и отдел/);
  assert.deepEqual(roleScope("department_head", organizationId, departmentId), {
    kind: "department", organization_id: organizationId, department_id: departmentId,
  });
});

test("поиск пользователей не вставляет HTML и ищет логин и email", () => {
  const users = [
    { display_name: "Иван Мейн", login: "i.mein", email: "a@example.test" },
    { display_name: "Мария", login: "m.s", email: "b@example.test" },
  ];
  assert.deepEqual(filterUsers(users, "I.MEIN"), [users[0]]);
  assert.deepEqual(filterUsers(users, "b@example"), [users[1]]);
  assert.deepEqual(filterUsers(users, "неизвестный"), []);
});

test("админка разрешает менять локального администратора и блокирует AD назначения", () => {
  const worker = { role: "designer", source: "local", scope: { kind: "own" } };
  const admin = { role: "platform_admin", source: "local", scope: { kind: "platform" } };
  const external = { role: "department_head", source: "ad_group",
    scope: { kind: "department", organization_id: "org", department_id: "dept" } };
  assert.deepEqual(roleEditorState(["designer"], [worker]), {
    currentRole: "designer", scope: worker.scope, roleLocked: false,
  });
  assert.equal(roleEditorState(["designer", "platform_admin"], [worker, admin]).roleLocked, false);
  assert.equal(roleEditorState(["department_head"], [external]).roleLocked, true);
});

test("код подтверждения читается из fragment и удаляется из адресной строки", () => {
  let replaced = "";
  const history = { replaceState: (_state, _title, url) => { replaced = url; } };
  const token = consumeVerificationToken({
    href: "https://pdrd.example/account.html#verify_email=opaque%2Btoken",
  }, history);
  assert.equal(token, "opaque+token");
  assert.equal(replaced, "/account.html");
});

test("админский список использует серверную пагинацию", async () => {
  let url;
  globalThis.fetch = async (value) => {
    url = value;
    return new Response('{"items":[],"total":120,"limit":50,"offset":50}', {
      status: 200,
    });
  };
  const page = await listUsers({ limit: 50, offset: 50 });
  assert.equal(url, "/api/v1/admin/users?limit=50&offset=50");
  assert.equal(page.total, 120);
  assert.deepEqual(page.items, []);
});

test("пакеты документов остаются недоступны даже при серверном permission", async () => {
  const previousFetch = globalThis.fetch;
  const root = new FakeElement();
  const packages = new FakeElement();
  const accordion = new FakeElement("details");
  packages.selectors.set("[data-user-packages-accordion]", accordion);
  root.selectors.set("[data-user-packages-block]", packages);
  root.selectors.set("[data-normative-prompt-block]", new FakeElement());
  root.selectors.set("[data-experience-nav]", new FakeElement());
  globalThis.fetch = async () => new Response(JSON.stringify({
    authenticated: true,
    user: { user_id: "u1", roles: ["designer"], permissions: [
      "user_documents.own.read", "user_documents.own.write",
    ] },
  }), { status: 200 });
  try {
    bindMainAccess(root);
    await refreshSession();
    assert.equal(accordion.inert, true);
    assert.equal(packages.getAttribute("aria-disabled"), "true");
  } finally {
    globalThis.fetch = previousFetch;
    clearSession();
  }
});
