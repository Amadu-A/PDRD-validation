// frontend/tests/helpers/fake-dom.js

/** Минимальный DOM для функциональных тестов: классы, подписки, дерево и pointer capture. */

export class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.selectors = new Map();
    this.lists = new Map();
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.rectangle = { left: 10, top: 20, width: 800, height: 500 };
    this.captures = new Set();
    const classes = () => new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList = {
      contains: (name) => classes().has(name),
      add: (...names) => { this.className = [...new Set([...classes(), ...names])].join(" "); },
      remove: (...names) => { this.className = [...classes()].filter((name) => !names.includes(name)).join(" "); },
      toggle: (name, force) => {
        const add = force ?? !classes().has(name);
        this.classList[add ? "add" : "remove"](name);
        return add;
      },
    };
  }

  append(...nodes) {
    for (const node of nodes) {
      node.remove();
      node.parent = this;
      node.removed = false;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.append(...nodes);
  }

  prepend(...nodes) {
    for (const node of [...nodes].reverse()) {
      node.remove();
      node.parent = this;
      node.removed = false;
      this.children.unshift(node);
    }
  }

  insertBefore(node, before) {
    const index = this.children.indexOf(before);
    this.children.splice(index >= 0 ? index : this.children.length, 0, node);
    node.parent = this;
  }

  remove() {
    this.removed = true;
    if (this.parent) {
      this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
  }

  addEventListener(name, handler) { this.listeners.set(name, [...(this.listeners.get(name) ?? []), handler]); }
  removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) ?? []).filter((item) => item !== handler)); }
  dispatch(name, event = {}) { for (const handler of this.listeners.get(name) ?? []) handler(event); }
  click() { this.dispatch("click"); }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  getBoundingClientRect() { return this.rectangle; }
  setPointerCapture(id) { this.captures.add(id); }
  hasPointerCapture(id) { return this.captures.has(id); }
  releasePointerCapture(id) { this.captures.delete(id); }

  matches(selector) {
    const data = /\[data-([\w-]+)\]/.exec(selector);
    if (data) {
      const key = data[1].replace(/-([a-z])/g, (_match, char) => char.toUpperCase());
      if (this.dataset[key] === undefined) return false;
    }
    return [...selector.matchAll(/\.([\w-]+)/g)].every((match) => this.classList.contains(match[1]));
  }

  querySelector(selector) { return this.selectors.get(selector) ?? this.querySelectorAll(selector)[0] ?? null; }
  querySelectorAll(selector) {
    if (this.lists.has(selector)) return this.lists.get(selector);
    const result = [];
    for (const child of this.children) {
      if (selector.split(",").some((part) => child.matches(part.trim()))) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
}
