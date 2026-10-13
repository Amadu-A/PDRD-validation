// frontend/src/js/features/analysis/finding-provenance.js

/** Согласует сохранённые источники и переходы к доказательствам замечания. */

/** Выбирает непустой сохранённый массив; пустой основной не скрывает резервный. */
export function preferredSourceArray(primary, fallback) {
  if (Array.isArray(primary) && primary.length) return primary;
  return Array.isArray(fallback) ? fallback : [];
}

/** Происхождение берётся только из сохранённых массивов источников. */
export function findingSourceKinds(finding) {
  const arrays = [
    ["D", finding.document_context_basis_sources],
    ["N", preferredSourceArray(finding.basis_sources, finding.normative_sources)],
    ["T", preferredSourceArray(finding.technical_assignment_basis_sources, finding.technical_assignment_sources)],
    ["U", preferredSourceArray(finding.user_package_basis_sources, finding.user_package_sources)],
    ["E", finding.experience_sources],
    ["EQ", finding.equipment_documentation_basis_sources],
  ];
  return arrays.filter(([, values]) => Array.isArray(values) && values.length).map(([kind]) => kind);
}

/** Объясняет происхождение в полном отчёте, не занимая место на чертеже. */
export function findingSourceDescription(finding) {
  const descriptions = {
    N: "N — сохранённое нормативное основание (ГОСТ, СП, ПУЭ и другие нормативы).",
    T: "T — сохранённое требование технического задания; это не нормативное основание.",
    U: "U — сохранённый источник из пользовательских документов; это не нормативное основание.",
    D: "D — факты проверяемого PDF. Они подтверждают содержание и внутренние противоречия, но не определяют правильное значение и не заменяют норматив.",
    E: "E — сохранённый инженерный опыт для повторной проверки и формулировки; он не доказывает нарушение.",
    EQ: "EQ — сохранённая техническая документация производителя для указанной модели и ревизии.",
  };
  const kinds = findingSourceKinds(finding);
  const lines = kinds.length
    ? kinds.map((kind) => descriptions[kind])
    : ["Инженерное замечание: сохранённые источники N/T/U/D/E отсутствуют. Вывод основан на данных листа и требует проверки инженером."];
  if (Array.isArray(finding.project_context_sources) && finding.project_context_sources.length) {
    lines.push("ПЗ — контекст пояснительной записки; он не является нормативным доказательством.");
  }
  return lines.join("\n");
}

/** Физические страницы одного логического замечания, без повторов. */
export function findingPages(finding, fallback = 1) {
  const pages = [finding.page ?? finding.page_number ?? fallback,
    ...(finding.evidence_locations ?? []).map((location) => location.page),
    ...(finding.document_context_basis_sources ?? []).map((source) => source.page)];
  return [...new Set(pages.map(Number).filter((page) => Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
}

/** Русская подпись для отчёта и карточек. */
export function findingPageLabel(finding, fallback = 1) {
  const pages = findingPages(finding, fallback);
  return `${pages.length > 1 ? "Страницы" : "Страница"} ${pages.join(", ")}`;
}

/** Переходит к странице и подсвечивает сохранённые области этого замечания. */
export function focusDocumentEvidence(link, findingId, pageNumber) {
  link.addEventListener("click", async (event) => {
    event?.preventDefault?.();
    await focusPageEvidence(pageNumber, { findingId });
  });
}

const navigationRequests = new WeakMap();
const focusTimers = new WeakMap();

/** Разрешает только внешние HTTP(S)-адреса без учётных данных и локальных хостов. */
export function safeExternalSourceUrl(value) {
  try {
    const url = new URL(String(value || ""));
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password
        || !host || host === "localhost" || /\.(?:localhost|local|internal)$/.test(host)
        || /^(?:0\.|10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/.test(host)
        || /^\[(?:::|fc|fd|fe[89ab])/i.test(host)) return null;
    return url.href;
  } catch { return null; }
}

/** Переходит в существующий renderer; подсветка начинается только после загрузки изображения. */
export async function focusPageEvidence(pageNumber, { findingId = null, regions = [] } = {}) {
  const number = Number(pageNumber);
  if (!Number.isInteger(number) || number < 1) return false;
  const page = document.getElementById(`analysis-page-${number}`);
  if (!page) return false;
  const request = Symbol();
  navigationRequests.set(page, request);
  page.scrollIntoView({ behavior: "smooth", block: "start" });
  const image = page.querySelector(".analysis-result__page-image");
  if (image && !image.complete) {
    image.loading = "eager";
    await new Promise((resolve) => {
      const finish = () => { clearTimeout(timer); image.removeEventListener("load", finish); image.removeEventListener("error", finish); resolve(); };
      const timer = setTimeout(finish, 15000);
      image.addEventListener("load", finish, { once: true });
      image.addEventListener("error", finish, { once: true });
    });
  }
  if (image?.decode && image.complete && image.naturalWidth > 0) {
    try { await image.decode(); } catch { /* Не подменяем ошибку изображения координатами. */ }
  }
  if (navigationRequests.get(page) !== request) return false;
  if (image && (!image.complete || image.naturalWidth === 0)) {
    evidenceNavigationMessage(page).textContent = "Изображение листа не загрузилось. Повторите переход после загрузки.";
    return false;
  }
  if (globalThis.requestAnimationFrame && page.isConnected) await new Promise((resolve) => requestAnimationFrame(resolve));
  if (navigationRequests.get(page) !== request) return false;
  for (const previous of page.querySelectorAll(".is-document-evidence-focus")) {
    previous.classList.remove("is-document-evidence-focus");
    clearTimeout(focusTimers.get(previous)); focusTimers.delete(previous);
    if (previous.classList.contains("analysis-result__equipment-evidence")) previous.remove();
  }
  const highlighted = [];
  if (findingId !== null) {
    for (const node of page.querySelectorAll(".analysis-result__bbox")) {
      if (node.dataset.findingId === String(findingId)) highlighted.push(node);
    }
  } else {
    for (const previous of page.querySelectorAll(".analysis-result__equipment-evidence")) previous.remove();
    const pane = page.querySelector(".analysis-result__page-image-pane");
    for (const region of regions) {
      const box = region?.bbox || region;
      if (!pane || !validEvidenceBox(box)) continue;
      const node = document.createElement("div");
      node.className = "analysis-result__bbox analysis-result__equipment-evidence";
      node.setAttribute("aria-hidden", "true");
      node.style.left = `${box.x_min / 10}%`; node.style.top = `${box.y_min / 10}%`;
      node.style.width = `${(box.x_max - box.x_min) / 10}%`; node.style.height = `${(box.y_max - box.y_min) / 10}%`;
      pane.append(node); highlighted.push(node);
    }
  }
  const message = evidenceNavigationMessage(page);
  message.textContent = highlighted.length ? "Показано место выбранного замечания или оборудования." : "Точное место не определено";
  const targets = [...highlighted];
  if (highlighted.length && findingId !== null) {
    for (const node of page.querySelectorAll(".analysis-result__annotation, .analysis-result__group-member")) {
      if (node.dataset.findingId !== String(findingId)) continue;
      targets.push(node);
      const group = node.closest?.(".analysis-result__annotation--group");
      if (group) targets.push(group);
    }
  }
  const scheduleTimeout = globalThis.window?.setTimeout?.bind(globalThis.window) || setTimeout;
  for (const node of new Set(targets)) {
    node.classList.add("is-document-evidence-focus");
    const timeout = scheduleTimeout(() => {
      node.classList.remove("is-document-evidence-focus");
      focusTimers.delete(node);
      if (node.classList.contains("analysis-result__equipment-evidence")) node.remove();
    }, 2500);
    focusTimers.set(node, timeout);
    timeout?.unref?.();
  }
  return true;
}

/** Сообщение перехода не меняет сохранённые координаты и состояние Review. */
function evidenceNavigationMessage(page) {
  let message = page.querySelector(".analysis-result__evidence-navigation-message");
  if (!message) {
    message = document.createElement("p"); message.className = "analysis-result__evidence-navigation-message";
    message.setAttribute("role", "status"); page.append(message);
  }
  return message;
}

function validEvidenceBox(box) {
  return box && [box.x_min, box.y_min, box.x_max, box.y_max]
    .every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1000)
    && box.x_min < box.x_max && box.y_min < box.y_max;
}

/** Строит путь к сохранённой версии только для канонических ID задания и EQ. */
export function equipmentSnapshotUrl(jobId, sourceId) {
  const job = String(jobId || "");
  const source = String(sourceId || "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(job)
      || !/^EQ-[0-9a-f]{32}$/i.test(source)) return null;
  return "/api/v1/analyses/" + encodeURIComponent(job)
    + "/equipment-documents/" + encodeURIComponent(source);
}
