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

test("без назначения ревью у проектировщика недоступны Gold и утверждение", () => {
  const state = { authenticated: true, user: {
    roles: ["designer"], permissions: ["analysis.run", "user_documents.own.write"],
  } };
  assert.equal(accessDescription(state), "Проектировщик");
  const result = partitionCapabilities(state);
  assert.ok(result.locked.includes("Создание Gold-замечаний"));
  assert.ok(result.locked.includes("Утверждение Human Review"));
  assert.ok(result.locked.includes("Собственные пользовательские документы (временно недоступны)"));
});

test("руководитель ограничен назначенными разделами, дизайнер своим владельцем", () => {
  assert.deepEqual(roleScope("designer"), { kind: "own" });
  assert.deepEqual(roleScope("platform_admin"), { kind: "platform" });
  assert.throws(() => roleScope("department_head"), /хотя бы один раздел/);
  assert.throws(() => roleScope("department_head", ["bad-id"]), /хотя бы один раздел/);
  assert.deepEqual(roleScope("department_head", ["11111111-1111-4111-8111-111111111111"]), { kind: "sections" });
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

test("пакеты доступны вошедшему владельцу с серверным permission", async () => {
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
    assert.equal(accordion.inert, false);
    assert.equal(packages.getAttribute("aria-disabled"), "false");
  } finally {
    globalThis.fetch = previousFetch;
    clearSession();
  }
});


test("руководитель видит Experience и создание разделов, а удаление доступно только админу", async () => {
  const previousFetch = globalThis.fetch;
  const root = new FakeElement(); const packages = new FakeElement();
  packages.selectors.set("[data-user-packages-accordion]", new FakeElement("details"));
  root.selectors.set("[data-user-packages-block]", packages);
  root.selectors.set("[data-normative-prompt-block]", new FakeElement());
  const experience = new FakeElement(); const deleting = new FakeElement("button");
  root.selectors.set("[data-experience-nav]", experience);
  root.selectors.set("[data-normative-section-delete]", deleting);
  let permissions = ["experience.catalog.read", "normative.write"];
  globalThis.fetch = async () => new Response(JSON.stringify({ authenticated: true,
    user: { user_id: "head", roles: ["department_head"], permissions } }), { status: 200 });
  try {
    bindMainAccess(root); await refreshSession();
    assert.equal(experience.hidden, false);
    assert.equal(root.dataset.canWriteNormative, "true");
    assert.equal(deleting.hidden, true);
    const trash = { closest: () => trash };
    let blocked = 0;
    const click = { target: trash, preventDefault() { blocked += 1; }, stopImmediatePropagation() {} };
    root.dispatch("click", click);
    assert.equal(blocked, 1);
    assert.equal(root.dataset.canDeleteNormative, "false");
    permissions = [...permissions, "normative.delete"]; await refreshSession();
    assert.equal(deleting.hidden, false);
    root.dispatch("click", click);
    assert.equal(blocked, 1);
    assert.equal(root.dataset.canDeleteNormative, "true");
    clearSession(); assert.equal(experience.hidden, true); assert.equal(deleting.hidden, true);
  } finally { globalThis.fetch = previousFetch; clearSession(); }
});
