// frontend/src/js/features/review/access.js

/** Связывает серверные права Review с редактором и PDF без доступа для гостя. */
import { hasPermission } from "../auth/access.js";
import { currentSession } from "../auth/session.js";

export function reviewCapabilities() {
  const session = currentSession();
  return {
    canCreateGold: hasPermission(session, "review.gold.create"),
    canDecide: hasPermission(session, "review.findings.decide"),
    canDownloadReview: hasPermission(session, "review.pdf.download"),
  };
}

export function mountAuthorizedReview(controller, root, options) {
  if (reviewCapabilities().canCreateGold) controller.mount(root, options);
}
