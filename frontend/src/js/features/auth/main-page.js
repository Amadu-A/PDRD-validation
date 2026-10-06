// frontend/src/js/features/auth/main-page.js

/** Подключает идентичность к уже созданным разделам главной страницы. */
import { hasPermission } from "./access.js";
import { bindSiteHeader } from "./header.js";
import { bindMainAccess } from "./main-access.js";
import { subscribeSession } from "./session.js";

export function bindMainIdentity({ root, normativeCatalog, promptEditor, userPackageCatalog }) {
  bindMainAccess(root);
  const header = root.querySelector("[data-site-header]");
  void bindSiteHeader(header).catch(() => {
    const identity = header.querySelector("[data-header-identity]");
    identity.hidden = false;
    identity.textContent = "Сервис входа временно недоступен";
  });
  let previousIdentity = null;
  subscribeSession((session) => {
    const identity = session.user ? `${session.user.user_id}:${session.user.authorization_version}` : null;
    if (identity !== previousIdentity) {
      previousIdentity = identity;
      promptEditor.reset();
      void userPackageCatalog?.reset();
      void normativeCatalog.reset();
      return;
    }
    const sectionId = normativeCatalog.getSelection()?.sectionId;
    void userPackageCatalog?.setSection(hasPermission(session, "user_documents.own.read") ? sectionId : null);
    if ((hasPermission(session, "working_prompt.use") || hasPermission(session, "system_prompt.manage")) && sectionId) {
      void promptEditor.setSection(sectionId);
    }
  });
}
