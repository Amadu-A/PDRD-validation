// frontend/src/js/features/portal/navigation.js

/** Синхронизирует текущий раздел боковой навигации с URL fragment. */
export function bindPortalNavigation(root, { showSections = false } = {}) {
  const links = [...root.querySelectorAll('a[href^="#"]')];
  const sections = showSections
    ? [...document.querySelectorAll("[data-admin-section]")]
    : [];

  function activate() {
    const requested = window.location.hash.slice(1);
    const known = links.some((link) => link.getAttribute("href") === `#${requested}`);
    const sectionId = known ? requested : "overview";
    for (const link of links) {
      const selected = link.getAttribute("href") === `#${sectionId}`;
      if (selected) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
    for (const section of sections) {
      section.hidden = section.dataset.adminSection !== sectionId;
    }
  }

  window.addEventListener("hashchange", activate);
  activate();
  return activate;
}
