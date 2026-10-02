// frontend/src/js/features/admin/page.js

/** Административная страница: доступ проверяется сессией и admin-service. */
import { isAdmin } from "../auth/access.js";
import { bindSiteHeader } from "../auth/header.js";
import { subscribeSession } from "../auth/session.js";
import { bindPortalNavigation } from "../portal/navigation.js";
import { listUsers } from "./api.js";
import { bindDirectoryPage } from "./directory-page.js";
import { renderUsers } from "./users.js";

const status = document.querySelector("[data-admin-status]");
const content = document.querySelector("[data-admin-content]");
let users = [];
let total = 0;
let offset = 0;
const limit = 50;
let loadingPage = false;
let controlsBound = false;
let active = false;
let accessRevision = 0;
let directory = null;

async function reloadUsers(revision = accessRevision) {
  const page = await listUsers({ limit, offset });
  if (revision !== accessRevision) return;
  users = page.items;
  total = page.total;
  renderUsers(document, users, reloadUsers);
  document.querySelector("[data-admin-overview]").textContent =
    `В каталоге пользователей: ${total}. Изменения ролей записываются через admin-service.`;
  document.querySelector("[data-admin-prev]").disabled = offset === 0;
  document.querySelector("[data-admin-next]").disabled = offset + users.length >= total;
  document.querySelector("[data-admin-page-label]").textContent = total
    ? `${offset + 1}–${offset + users.length} из ${total}` : "Нет пользователей";
}

async function changePage(nextOffset) {
  if (loadingPage) return;
  const revision = accessRevision;
  loadingPage = true;
  const previousOffset = offset;
  offset = nextOffset;
  const previousButton = document.querySelector("[data-admin-prev]");
  const nextButton = document.querySelector("[data-admin-next]");
  previousButton.disabled = true;
  nextButton.disabled = true;
  try {
    await reloadUsers(revision);
    if (revision !== accessRevision) return;
    status.textContent = "";
  } catch (error) {
    if (revision !== accessRevision) return;
    offset = previousOffset;
    status.textContent = error.detail ?? "Не удалось перейти к странице пользователей.";
    previousButton.disabled = offset === 0;
    nextButton.disabled = offset + users.length >= total;
  } finally {
    loadingPage = false;
  }
}

export async function startAdminPage() {
  bindPortalNavigation(document.querySelector(".portal-nav"), { showSections: true });
  subscribeSession((session) => {
    const revision = ++accessRevision;
    void updateAccess(session, revision);
  });
  try {
    await bindSiteHeader(document.querySelector("[data-site-header]"));
  } catch {
    status.textContent = "Не удалось проверить сессию. Обновите страницу.";
    return;
  }
}

async function updateAccess(session, revision) {
  if (!isAdmin(session)) {
    active = false;
    offset = 0;
    content.hidden = true;
    status.textContent = "Для доступа к админке войдите с ролью администратора PDRD.";
    return;
  }
  content.hidden = false;
  status.textContent = "";
  if (!controlsBound) {
    controlsBound = true;
    directory = bindDirectoryPage(document.querySelector("[data-admin-directory]"));
    document.querySelector('a[href="#roles"]').addEventListener("click", () => {
      void directory.load();
    });
    document.querySelector("[data-admin-search]").addEventListener("input", () => {
      renderUsers(document, users, reloadUsers);
    });
    document.querySelector("[data-admin-prev]").addEventListener("click", () => {
      void changePage(Math.max(0, offset - limit));
    });
    document.querySelector("[data-admin-next]").addEventListener("click", () => {
      void changePage(offset + limit);
    });
  }
  if (active) return;
  active = true;
  if (window.location.hash === "#roles") void directory.load();
  try {
    await reloadUsers(revision);
  } catch (error) {
    if (revision !== accessRevision) return;
    active = false;
    status.textContent = error.detail ?? "Не удалось загрузить пользователей.";
  }
}

void startAdminPage();
