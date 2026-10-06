// frontend/src/js/features/auth/main-access.js

/** Отражает уровни доступа на главной странице; API проверяет права отдельно. */
import { hasPermission } from "./access.js";
import { subscribeSession } from "./session.js";

const normativeControls = [
  "[data-normative-section-create]", "[data-normative-section-rename]",
  "[data-normative-section-delete]", "[data-normative-category-create]",
  "[data-normative-upload-zone]", "[data-normative-file-input]",
  "[data-normative-tree] .normative-sidebar__category-actions",
  "[data-normative-tree] .normative-sidebar__document-actions",
].join(", ");

export function bindMainAccess(root) {
  let canWriteNormative = false;
  root.addEventListener("click", (event) => {
    if (canWriteNormative || !event.target.closest(normativeControls)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  root.addEventListener("keydown", (event) => {
    if (canWriteNormative || !["Enter", " "].includes(event.key)
      || !event.target.closest(normativeControls)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  subscribeSession((session) => {
    canWriteNormative = hasPermission(session, "normative.write");
    root.dataset.canWriteNormative = String(canWriteNormative);
    const packages = root.querySelector("[data-user-packages-block]");
    // UI отражает серверные права; UUID владельца браузер не задаёт.
    const canUsePackages = hasPermission(session, "user_documents.own.read");
    packages.querySelector("[data-user-packages-accordion]").inert = !canUsePackages;
    packages.setAttribute("aria-disabled", String(!canUsePackages));
    root.querySelector("[data-normative-prompt-block]").hidden =
      !hasPermission(session, "working_prompt.use") && !hasPermission(session, "system_prompt.manage");
    const experienceNavigation = root.querySelector("[data-experience-nav]");
    if (experienceNavigation) experienceNavigation.hidden =
      !hasPermission(session, "admin.access");
  });
}
