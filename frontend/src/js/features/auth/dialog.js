// frontend/src/js/features/auth/dialog.js

/** Управляет доступным модальным окном входа и регистрации без хранения пароля. */
import { login, register } from "./api.js";
import { refreshSession } from "./session.js";

export function bindAuthDialog(dialog) {
  const title = dialog.querySelector("[data-auth-title]");
  const message = dialog.querySelector("[data-auth-message]");
  const hint = dialog.querySelector("[data-auth-hint]");
  const loginForm = dialog.querySelector("[data-auth-login-form]");
  const registerForm = dialog.querySelector("[data-auth-register-form]");
  const tabs = [...dialog.querySelectorAll("[data-auth-tab]")];
  let pending = false;

  function select(kind) {
    loginForm.hidden = kind !== "login";
    registerForm.hidden = kind !== "register";
    title.textContent = kind === "login" ? "Вход" : "Регистрация";
    hint.textContent = kind === "login"
      ? "Введите локальное имя пользователя, корпоративный логин или email."
      : "После регистрации откройте письмо и подтвердите email.";
    message.textContent = "";
    for (const tab of tabs) tab.setAttribute(
      "aria-pressed", String(tab.dataset.authTab === kind),
    );
    (kind === "login" ? loginForm : registerForm)
      .querySelector("input")?.focus();
  }

  function open(kind = "login") {
    if (!dialog.open) dialog.showModal();
    select(kind);
  }

  for (const tab of tabs) tab.addEventListener("click", () => select(tab.dataset.authTab));
  dialog.querySelector("[data-auth-close]").addEventListener("click", () => dialog.close());

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    message.textContent = "Проверяем учётные данные…";
    const submit = loginForm.querySelector('[type="submit"]');
    const fields = loginForm.elements;
    submit.disabled = true;
    try {
      await login(fields.namedItem("login").value.trim(),
        fields.namedItem("password").value);
      fields.namedItem("password").value = "";
      await refreshSession();
      dialog.close();
    } catch (error) {
      message.textContent = error.detail ?? "Не удалось войти. Попробуйте позже.";
      if (error.status === 401) {
        hint.textContent = "Проверьте логин и пароль. Если у вас нет учётной записи, выберите «Регистрация».";
      }
      fields.namedItem("password").value = "";
    } finally {
      submit.disabled = false;
      pending = false;
    }
  });

  registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    message.textContent = "Создаём аккаунт…";
    const submit = registerForm.querySelector('[type="submit"]');
    submit.disabled = true;
    const fields = registerForm.elements;
    try {
      await register(fields.namedItem("display_name").value.trim(),
        fields.namedItem("email").value.trim(), fields.namedItem("password").value);
      message.textContent = "Проверьте почту: мы отправили ссылку для подтверждения email.";
      fields.namedItem("password").value = "";
    } catch (error) {
      message.textContent = error.detail ?? "Регистрация не удалась. Попробуйте позже.";
      fields.namedItem("password").value = "";
    } finally {
      submit.disabled = false;
      pending = false;
    }
  });

  return { open };
}
