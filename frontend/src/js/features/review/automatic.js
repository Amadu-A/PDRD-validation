// frontend/src/js/features/review/automatic.js

/**
 * Review-адаптер рамок VLM и общих карточек групп: скрытие отклонённых частей,
 * растягивание и Undo. Пользовательская геометрия — локальное предложение,
 * она не становится подтверждённой серверной областью VLM.
 */

import {
  getVisualizationReview,
  pinReviewCallout,
  reviewCalloutBox,
} from "../analysis/visualization-review.js";
import { positionBox } from "./geometry.js";
import { attachBoxResize } from "./resize.js";

function issueBox(entry) {
  return {
    x_min: entry.box.xMin, y_min: entry.box.yMin,
    x_max: entry.box.xMax, y_max: entry.box.yMax,
  };
}

function measuredBox(node, pane) {
  const rect = node.getBoundingClientRect();
  const bounds = pane.getBoundingClientRect();
  const clamp = (value) => Math.max(0, Math.min(1000, Math.round(value)));
  return {
    x_min: clamp((rect.left - bounds.left) * 1000 / bounds.width),
    y_min: clamp((rect.top - bounds.top) * 1000 / bounds.height),
    x_max: clamp((rect.left + rect.width - bounds.left) * 1000 / bounds.width),
    y_max: clamp((rect.top + rect.height - bounds.top) * 1000 / bounds.height),
  };
}

/** Отделяет автоматическую визуализацию от состояния решений и журнала листа. */
export function createAutomaticReview({ onGeometry, onRecord, onStart }) {
  let entries = [];
  let resizers = [];
  const calloutBoxes = new Map();

  function dispose() {
    for (const resize of resizers) resize.dispose();
    resizers = [];
    entries = [];
    calloutBoxes.clear();
  }

  function mount(visualization) {
    dispose();
    for (const page of visualization.querySelectorAll(".analysis-result__page-visualization")) {
      const adapter = getVisualizationReview(page);
      if (!adapter) continue;
      const callouts = new Set();
      for (const record of adapter.records) {
        if (!record.findingId) continue;
        const entry = {
          ...record, adapter, rejected: false,
          boxes: record.bboxEntries.map(issueBox), resizers: [],
        };
        entries.push(entry);

        record.bboxEntries.forEach((bbox, index) => {
          const apply = (box) => {
            Object.assign(bbox.box, {
              xMin: box.x_min, yMin: box.y_min,
              xMax: box.x_max, yMax: box.y_max,
            });
            positionBox(bbox.node, box);
            adapter.redraw();
          };
          const save = (box) => {
            entry.boxes[index] = { ...box };
            apply(box);
            onGeometry([record.findingId]);
          };
          const resize = attachBoxResize({
            node: bbox.node, page, label: "Область ошибки VLM",
            bounds: () => adapter.imagePane.getBoundingClientRect(),
            getBox: () => ({ ...entry.boxes[index] }),
            enabled: () => !entry.rejected && page.dataset.reviewSelecting !== "true",
            onStart() {
              cancelActive();
              onStart();
            },
            preview: apply,
            commit(box, previous) {
              save(box);
              onRecord(record.pageNumber, "изменение области VLM", () => save(previous));
            },
          });
          entry.resizers.push(resize);
          resizers.push(resize);
        });

        if (callouts.has(record.callout)) continue;
        callouts.add(record.callout);
        let previousPin = null;
        const members = () => entries.filter((item) => (
          item.callout === record.callout && !item.rejected
        ));
        const apply = (box) => {
          pinReviewCallout(record.callout, box);
          record.callout.dataset.reviewResized = String(Boolean(box));
          if (box) positionBox(record.callout, box);
          else {
            record.callout.style.width = "";
            record.callout.style.height = "";
          }
          adapter.redraw();
        };
        const save = (box) => {
          if (box) calloutBoxes.set(record.callout, { ...box });
          else calloutBoxes.delete(record.callout);
          apply(box);
          onGeometry(members().map((item) => item.findingId));
        };
        const resize = attachBoxResize({
          node: record.callout, page, label: "Карточка замечания VLM",
          scrollContent: true,
          minimum: { minWidth: 130, minHeight: 80 },
          bounds: () => adapter.imagePane.getBoundingClientRect(),
          getBox: () => reviewCalloutBox(record.callout) ?? measuredBox(record.callout, adapter.imagePane),
          enabled: () => members().length > 0 && page.dataset.reviewSelecting !== "true",
          onStart() {
            cancelActive();
            onStart();
            previousPin = reviewCalloutBox(record.callout);
          },
          preview: apply,
          rollback: () => apply(previousPin),
          commit(box) {
            const old = previousPin;
            save(box);
            onRecord(record.pageNumber, "изменение карточки VLM", () => save(old));
          },
        });
        entry.resizers.push(resize);
        resizers.push(resize);
      }
    }
  }

  function setRejected(findingId, rejected) {
    for (const entry of entries.filter((item) => item.findingId === findingId)) {
      if (rejected) for (const resize of entry.resizers) resize.cancel();
      entry.rejected = rejected;
      entry.item.classList.toggle("is-hidden", rejected);
      for (const bbox of entry.bboxEntries) bbox.node.classList.toggle("is-hidden", rejected);
      for (const connector of entry.connectorEntries) {
        connector.polyline.classList.toggle("is-hidden", rejected);
      }
      const allRejected = entries
        .filter((item) => item.callout === entry.callout)
        .every((item) => item.rejected);
      entry.callout.classList.toggle("is-hidden", allRejected);
      entry.adapter.redraw();
    }
  }

  function snapshot(findingId) {
    return entries.filter((entry) => entry.findingId === findingId).map((entry) => ({
      page_number: entry.pageNumber,
      proposed_issue_boxes: entry.boxes.map((box) => ({ ...box })),
      callout_box: calloutBoxes.has(entry.callout) ? { ...calloutBoxes.get(entry.callout) } : null,
    }));
  }

  function cancelActive() {
    for (const resize of resizers) resize.cancel();
  }

  return { mount, dispose, setRejected, snapshot, cancelActive };
}
