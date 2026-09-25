// frontend/src/js/features/experience/navigation.js

/** Добавляет переход к Experience рядом с существующей панелью анализа. */
export function mountExperienceNavigation(page) {
  if (page.querySelector("[data-experience-nav]")) {
    return;
  }

  const panel = document.createElement("aside");
  panel.className = "experience-nav";
  panel.dataset.experienceNav = "";
  panel.setAttribute("aria-labelledby", "experienceNavTitle");

  const title = document.createElement("h2");
  title.id = "experienceNavTitle";
  title.className = "experience-nav__title";
  title.textContent = "База опыта";

  const description = document.createElement("p");
  description.className = "experience-nav__description";
  description.textContent = "Замечания, проверенные инженером.";

  const link = document.createElement("a");
  link.className = "experience-nav__link";
  link.href = "/experience.html";
  link.textContent = "Открыть Experience →";
  link.dataset.experienceOpen = "";

  panel.append(title, description, link);
  page.append(panel);
}
