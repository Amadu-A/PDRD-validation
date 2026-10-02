// frontend/src/js/features/account/verification-link.js

/** Одноразовый код из URL fragment удаляется до сетевого запроса и навигации. */
export function consumeVerificationToken(location, history) {
  const url = new URL(location.href);
  const fragment = new URLSearchParams(url.hash.slice(1));
  const token = fragment.get("verify_email");
  if (!token) return null;
  history.replaceState(null, "", `${url.pathname}${url.search}`);
  return token;
}
