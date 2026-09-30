// frontend/src/js/components/icons.js

/** Общие SVG-иконки действий: одинаковая корзина в нормативке и Experience. */
export function createTrashIcon(dom = document) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = dom.createElementNS(namespace, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", width: "17", height: "17", "aria-hidden": "true" })) svg.setAttribute(key, value);
  const path = dom.createElementNS(namespace, "path");
  path.setAttribute("d", "M9 3h6l1 2h4v2h-1l-1 14H6L5 7H4V5h4l1-2zm"
    + "1.2 2h3.6l-.5-1h-2.6l-.5 1z" + "M7 7l.86 12h8.28L17 7H7z");
  svg.append(path);
  return svg;
}
