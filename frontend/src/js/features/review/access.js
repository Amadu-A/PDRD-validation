// frontend/src/js/features/review/access.js

/** Связывает серверные права Review с редактором и PDF без доступа для гостя. */
import { hasPermission } from "../auth/access.js";
import { currentSession, subscribeSession } from "../auth/session.js";

export function reviewCapabilities() {
  const session = currentSession();
  return {
    canCreateGold: hasPermission(session, "review.gold.create"),
    canDecide: hasPermission(session, "review.findings.decide"),
    canDownloadReview: hasPermission(session, "review.pdf.download"),
  };
}

export function mountAuthorizedReview(controller, root, options) {
  if (Object.values(reviewCapabilities()).some(Boolean)) {
    controller.mount(root, options);
    return true;
  }
  return false;
}

/** Подключает уже показанный отчёт после загрузки сессии, не сбрасывая его решения. */
export function bindAuthorizedReview(controller, { subscribe = subscribeSession } = {}) {
  let report = null;
  let mounted = false;
  let identity = null;
  function clear() {
    controller.clear();
    report = null;
    mounted = false;
  }
  const unsubscribe = subscribe((session) => {
    const nextIdentity = session.user?.user_id ?? null;
    if (report && mounted && (nextIdentity !== identity
      || !Object.values(reviewCapabilities()).some(Boolean))) {
      controller.clear();
      mounted = false;
    }
    identity = nextIdentity;
    if (report && !mounted) mounted = mountAuthorizedReview(controller, report.root, report.options);
    else if (report && mounted) controller.setCapabilities?.(reviewCapabilities());
  });
  return {
    mount(root, options) {
      clear();
      report = { root, options };
      mounted = mountAuthorizedReview(controller, root, options);
    },
    clear,
    dispose() { clear(); unsubscribe(); },
  };
}
