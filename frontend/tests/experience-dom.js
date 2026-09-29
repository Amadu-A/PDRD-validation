// frontend/tests/experience-dom.js

/** Минимальный DOM для событий каталога без сторонних browser dependencies. */
export class Node {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = new Map(); this.listeners = new Map(); this.textContent = "";
    this.value = ""; this.checked = false; this.disabled = false; this.hidden = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  after(...nodes) { this.afterNodes = nodes; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attributes.set(key, value); }
  removeAttribute(key) { this.attributes.delete(key); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  removeEventListener(name) { this.listeners.delete(name); }
  emit(name) { return this.listeners.get(name)?.({ preventDefault() {} }); }
  click() { return this.emit("click"); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.focused = true; }
  remove() { this.removed = true; }
}

export function setup() {
  const elements = new Map();
  const keys = ["filter", "rows", "count", "notice", "export", "previous", "next", "refresh",
    "caption", "pagination", "image-dialog", "large-image", "image-original", "image-close",
    "edit-dialog", "edit-form", "edit-error", "edit-save", "edit-reload", "edit-close",
    "rejection-fields", "source-text", "history-dialog", "history-content", "history-close"];
  keys.push("delete-selection", "selected-count", "select-all", "clear-selection", "select-missing", "build-version", "prepare-fine-tune",
    "version-kind", "version-model", "version", "version-rename", "version-delete", "active-vector", "active-model", "active-status", "active-section",
    "version-dialog", "version-form", "version-title", "version-summary", "version-model-field", "version-error", "version-close", "version-save",
    "version-status", "version-status-title", "version-build-status", "version-quality-status", "version-apply-status", "version-failure-status",
    "version-dataset", "quality-controls", "quality-file", "quality-submit", "quality-revoke");
  for (const key of keys) elements.set(`[data-experience-${key}]`, new Node());
  const filter = Object.fromEntries(["query", "tag", "decision", "active", "learning_use", "job_id", "section_id"].map((name) => [name, new Node()]));
  const fields = Object.fromEntries(["document_title", "text", "normative_basis", "normative_reference", "active", "rejection_reason", "negative_target", "section_id", "section_title"].map((name) => [name, new Node()]));
  const versionFields = Object.fromEntries(["name", "model"].map((name) => [name, new Node()]));
  elements.get("[data-experience-version-form]").elements = { namedItem: (name) => versionFields[name] };
  elements.get("[data-experience-version-kind]").value = "vector";
  elements.get("[data-experience-filter]").fields = filter;
  elements.get("[data-experience-filter]").elements = { namedItem: (name) => filter[name] };
  elements.get("[data-experience-edit-form]").elements = { namedItem: (name) => fields[name] };
  const dom = { querySelector: (selector) => elements.get(selector), createElement: (tag) => new Node(tag), createElementNS: (_namespace, tag) => new Node(tag) };
  globalThis.document = dom;
  globalThis.window = { location: { href: "http://192.168.55.3:8080/experience.html" } };
  globalThis.FormData = class { constructor(form) { this.form = form; } entries() { return Object.entries(this.form.fields).map(([name, field]) => [name, field.value]); } };
  return { dom, filter, fields, versionFields, get: (key) => elements.get(`[data-experience-${key}]`) };
}

export function record(fields = {}) {
  return { id: "example-1", job_id: "job-1", document_id: "document-1", revision: 0,
    tag: "edited", decision: "rejected", learning_use: "needs_adjudication", active: true,
    requested_active: true, source_current: true, text: "Полный исправленный текст", normative_basis: "СП 1",
    normative_reference: "", document_title: "План", source_filename: "План.pdf", page_number: 2,
    created_at: "2026-09-28T10:00:00Z", crops: [{ width: 200, height: 100 }, { width: 300, height: 200 }],
    source: { created_by: "engineer:1", original_text: "Исходный текст VLM", text: "Исправление Review",
      finding_id: "vlm:1", approved_revision: 5, confirmed_by: "engineer:1" }, ...fields };
}

export const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
