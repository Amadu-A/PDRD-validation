// frontend/src/js/features/auth/header.js

/** Показывает действия входа и навигацию из серверной сессии. */
import { logout } from "./api.js";
import { isAdmin } from "./access.js";
import { bindAuthDialog } from "./dialog.js";
import { clearSession, refreshSession, subscribeSession } from "./session.js";

export function bindSiteHeader(root, { navigate = (url) => { window.location.href = url; } } = {}) {
  const loginButton = root.querySelector("[data-open-login]");
  const registerButton = root.querySelector("[data-open-register]");
  const logoutButton = root.querySelector("[data-header-logout]");
  const identity = root.querySelector("[data-header-identity]");
  const accountLink = root.querySelector("[data-account-link]");
  const adminLink = root.querySelector("[data-admin-link]");
  const dialog = document.querySelector("[data-auth-dialog]");
  const authDialog = dialog ? bindAuthDialog(dialog) : null;

  function open(kind) {
    if (authDialog) authDialog.open(kind);
    else navigate(`/?auth=${encodeURIComponent(kind)}`);
  }

  loginButton.addEventListener("click", () => open("login"));
  registerButton.addEventListener("click", () => open("register"));
  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    try {
      await logout();
      clearSession();
      navigate("/");
    } catch (error) {
      logoutButton.disabled = false;
      window.alert(error.detail ?? "Не удалось выйти. Попробуйте ещё раз.");
    }
  });

  subscribeSession((state) => {
    const signedIn = state.authenticated;
    loginButton.hidden = signedIn;
    registerButton.hidden = signedIn;
    logoutButton.hidden = !signedIn;
    identity.hidden = !signedIn;
    identity.textContent = signedIn ? state.user.display_name : "";
    accountLink.hidden = !signedIn;
    adminLink.hidden = !isAdmin(state);
  });

  const params = new URLSearchParams(window.location.search);
  const requested = params.get("auth");
  if (requested === "login" || requested === "register") {
    params.delete("auth");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    open(requested);
  }
  return refreshSession();
}
