// frontend/src/js/features/account/page.js

/** Производственный личный кабинет на основе проверенной серверной сессии. */
import { verifyEmail } from "../auth/api.js";
import { createAnalysisHistory } from "../analysis/history.js";
import { accessDescription, isAdmin } from "../auth/access.js";
import { bindSiteHeader } from "../auth/header.js";
import { currentSession, subscribeSession } from "../auth/session.js";
import { bindPortalNavigation } from "../portal/navigation.js";
import { renderCapabilities } from "./capabilities.js";
import { renderSessions } from "./sessions.js";
import { renderEquipmentSources } from "./equipment-sources.js";
import { consumeVerificationToken } from "./verification-link.js";

const status = document.querySelector("[data-account-status]");

function renderAccount(state) {
  const user = state.user;
  const tier = accessDescription(state);
  document.querySelector("[data-account-name]").textContent = user?.display_name
    ?? "Гостевой анализ без учётной записи";
  document.querySelector("[data-account-description]").textContent = state.authenticated
    ? "Ваш профиль и права загружены из серверной сессии PDRD."
    : "Загрузите документы, получите автозамечания и скачайте результат без регистрации.";
  const badge = document.querySelector("[data-account-tier]");
  badge.textContent = tier;
  badge.dataset.tone = state.authenticated ? "active" : "guest";
  document.querySelector("[data-account-meta]").textContent = state.authenticated
    ? "Полномочия проверяются при каждом действии"
    : "Гостевые замечания не сохраняются в Review и Experience";
  for (const [selector, value] of [
    ["[data-profile-name]", user?.display_name],
    ["[data-profile-email]", user?.email],
    ["[data-profile-login]", user?.login],
    ["[data-profile-status]", user?.status],
  ]) document.querySelector(selector).textContent = value || "—";
  document.querySelector("[data-profile-note]").textContent = state.authenticated
    ? "Пароль корпоративной учётной записи в PDRD не хранится."
    : "Профиль появится после регистрации или корпоративного входа.";
  document.querySelector("[data-account-admin-link]").hidden = !isAdmin(state);
  document.querySelector("[data-account-history-link]").hidden = !state.authenticated;
  renderCapabilities(document, state);
}

async function confirmEmailFromLink(token) {
  if (!token) return;
  status.textContent = "Подтверждаем email…";
  try {
    await verifyEmail(token);
    status.textContent = "Email подтверждён. Теперь можно войти.";
  } catch (error) {
    status.textContent = error.detail ?? "Не удалось подтвердить email. Ссылка могла устареть.";
  }
}

export async function startAccountPage() {
  createAnalysisHistory(document.querySelector("[data-analysis-history]"));
  const verificationToken = consumeVerificationToken(window.location, window.history);
  bindPortalNavigation(document.querySelector(".portal-nav"));
  subscribeSession((session) => {
    renderAccount(session);
    void renderSessions(document, session);
    void renderEquipmentSources(document, session);
  });
  try {
    await bindSiteHeader(document.querySelector("[data-site-header]"));
    if (!verificationToken) {
      status.textContent = currentSession().authenticated ? "" : "Вы просматриваете гостевой режим.";
    }
  } catch {
    status.textContent = "Не удалось проверить сессию. Обновите страницу.";
  }
  await confirmEmailFromLink(verificationToken);
}

void startAccountPage();
