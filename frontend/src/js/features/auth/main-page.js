// frontend/src/js/features/auth/main-page.js

/** Подключает идентичность к уже созданным разделам главной страницы. */
import { hasPermission } from "./access.js";
import { bindSiteHeader } from "./header.js";
import { bindMainAccess } from "./main-access.js";
import { subscribeSession } from "./session.js";

export function bindMainIdentity({ root, normativeCatalog, promptEditor }) {
  bindMainAccess(root);
  const header = root.querySelector("[data-site-header]");
  void bindSiteHeader(header).catch(() => {
    const identity = header.querySelector("[data-header-identity]");
    identity.hidden = false;
    identity.textContent = "Сервис входа временно недоступен";
  });
  subscribeSession((session) => {
    const sectionId = normativeCatalog.getSelection()?.sectionId;
    if (hasPermission(session, "system_prompt.manage") && sectionId) {
      void promptEditor.setSection(sectionId);
    }
  });
}
