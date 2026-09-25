// frontend/src/js/features/analysis/visualization.js

/**
 * Визуализация findings поверх rendered PDF pages.
 *
 * BBox приходит в нормализованных координатах 0..1000.
 * Один finding может содержать несколько visual regions.
 * Несколько findings одного явно обозначенного объекта могут делить
 * одну карточку; каждый подпункт сохраняет свой ID, sources и подсветку.
 * Карточки размещаются поверх листа автоматически.
 * Неподтверждённые гипотезы не получают рамки локализации.
 * Текст и source data вставляются только через textContent.
 */

const CALLOUT_MARGIN_PX = 10;
const CALLOUT_GAP_PX = 14;
const CARD_OVERLAP_WEIGHT = 12;
const REGION_OVERLAP_WEIGHT = 5;
const DISTANCE_WEIGHT = 0.015;
const GROUP_AREA_GAP = 100;

const GENERIC_FINDING_PREFIX = (
  "на листе выявлено несоответствие"
);

let tooltipSequence = 0;


function createElement(
  tagName,
  className = "",
  text = null,
) {
  const node = document.createElement(
    tagName,
  );

  if (className) {
    node.className = className;
  }

  if (
    text !== null
    && text !== undefined
  ) {
    node.textContent = String(
      text,
    );
  }

  return node;
}


function normalizedPage(
  value,
) {
  const page = Number(
    value,
  );

  if (
    !Number.isInteger(
      page,
    )
    || page < 1
  ) {
    return null;
  }

  return page;
}


function normalizeRawBox(
  rawBox,
  region = null,
) {
  if (!rawBox) {
    return null;
  }

  const box = {
    xMin: Number(
      rawBox.x_min,
    ),
    yMin: Number(
      rawBox.y_min,
    ),
    xMax: Number(
      rawBox.x_max,
    ),
    yMax: Number(
      rawBox.y_max,
    ),
    label: (
      region?.label
      ?? null
    ),
    source: (
      region?.source
      ?? null
    ),
  };

  const values = [
    box.xMin,
    box.yMin,
    box.xMax,
    box.yMax,
  ];

  if (
    values.some(
      (value) => (
        !Number.isFinite(
          value,
        )
        || value < 0
        || value > 1000
      ),
    )
    || box.xMin >= box.xMax
    || box.yMin >= box.yMax
  ) {
    return null;
  }

  return box;
}


function normalizedBoxes(
  finding,
) {
  const location = (
    finding?.location
  );

  if (
    location?.status !== "located"
  ) {
    return [];
  }

  const rawRegions = (
    Array.isArray(
      location.regions,
    )
      ? location.regions
      : []
  );

  const regionBoxes = rawRegions
    .map(
      (region) => (
        normalizeRawBox(
          region?.bbox,
          region,
        )
      ),
    )
    .filter(
      Boolean,
    );

  if (regionBoxes.length) {
    return regionBoxes;
  }

  const legacyBox = normalizeRawBox(
    location.bbox,
  );

  return (
    legacyBox
      ? [
        legacyBox,
      ]
      : []
  );
}


function pageLocationMap(
  page,
) {
  const locations = (
    Array.isArray(
      page.locations,
    )
      ? page.locations
      : []
  );

  return new Map(
    locations
      .filter(
        (location) => (
          location
          && typeof location
          === "object"
        ),
      )
      .map(
        (location) => [
          String(
            location.finding_id
            ?? "",
          ),
          location,
        ],
      ),
  );
}


function findingsForPage(
  payload,
  page,
) {
  if (!Array.isArray(
    payload.findings,
  )) {
    return [];
  }

  const pageNumber = normalizedPage(
    page.page_number,
  );

  if (pageNumber === null) {
    return [];
  }

  const locations = pageLocationMap(
    page,
  );

  return payload.findings
    .filter((finding) => finding.status !== "hypothesis")
    .map(
      (
        finding,
        findingIndex,
      ) => ({
        finding,
        findingIndex,
      }),
    )
    .filter(
      ({
        finding,
      }) => (
        normalizedPage(
          finding.page
          ?? finding.page_number,
        ) === pageNumber
      ),
    )
    .map(
      ({
        finding,
        findingIndex,
      }) => {
        const findingId = String(
          finding.finding_id
          ?? "",
        );

        return {
          finding: {
            ...finding,
            location: (
              locations.get(
                findingId,
              )
              ?? finding.location
              ?? {
                status: "unlocated",
                bbox: null,
                regions: [],
                confidence: 0,
                method: "unlocated",
              }
            ),
          },
          findingIndex,
        };
      },
    );
}


function preferredSourceArray(
  primary,
  fallback,
) {
  if (
    Array.isArray(
      primary,
    )
    && primary.length
  ) {
    return primary;
  }

  if (Array.isArray(
    fallback,
  )) {
    return fallback;
  }

  return [];
}


function sourceLine(
  source,
  fallbackName,
) {
  if (
    !source
    || typeof source !== "object"
  ) {
    return "";
  }

  const fileName = (
    source.source_file
    || source.file_name
    || source.source
    || source.source_id
    || fallbackName
  );

  const parts = [
    String(
      fileName,
    ),
  ];

  const page = (
    source.page
    ?? source.page_number
  );

  if (
    page !== null
    && page !== undefined
    && page !== ""
  ) {
    parts.push(
      `стр. ${page}`,
    );
  }

  if (source.point_id) {
    parts.push(
      `п. ${source.point_id}`,
    );
  }

  if (
    source.score !== null
    && source.score !== undefined
  ) {
    parts.push(
      `score=${source.score}`,
    );
  }

  return parts.join(
    ", ",
  );
}


function sourceListText(
  sources,
  fallbackName,
) {
  if (!Array.isArray(
    sources,
  )) {
    return "";
  }

  const values = sources
    .map(
      (source) => (
        sourceLine(
          source,
          fallbackName,
        )
      ),
    )
    .filter(
      Boolean,
    );

  return [
    ...new Set(
      values,
    ),
  ].join(
    "\n",
  );
}


function findingComment(
  finding,
) {
  return String(
    finding.comment
    || finding.message
    || finding.issue_text
    || "",
  ).trim();
}


function findingDisplayText(
  finding,
) {
  const comment = findingComment(
    finding,
  );

  const evidence = String(
    finding.evidence
    || "",
  ).trim();

  const normalizedComment = comment
    .toLocaleLowerCase(
      "ru-RU",
    )
    .replace(
      /\s+/g,
      " ",
    )
    .trim();

  if (
    evidence
    && normalizedComment.startsWith(
      GENERIC_FINDING_PREFIX,
    )
  ) {
    return evidence;
  }

  return (
    comment
    || evidence
    || "Текст замечания не передан."
  );
}


function projectContextText(
  finding,
) {
  const sources = (
    Array.isArray(
      finding.project_context_sources,
    )
      ? finding.project_context_sources
      : []
  );

  return sources
    .map(
      (source) => {
        if (
          !source
          || typeof source !== "object"
        ) {
          return "";
        }

        const parts = [
          String(
            source.source_id
            || "PZ",
          ),
        ];

        if (
          source.page !== null
          && source.page !== undefined
        ) {
          parts.push(
            `стр. ${source.page}`,
          );
        }

        if (
          source.score !== null
          && source.score !== undefined
        ) {
          parts.push(
            `score=${source.score}`,
          );
        }

        return parts.join(
          ", ",
        );
      },
    )
    .filter(
      Boolean,
    )
    .join(
      "\n",
    );
}


function localizationText(
  finding,
) {
  const location = finding.location;

  if (
    location?.status !== "located"
  ) {
    return (
      "Точное место на листе "
      + "автоматически не определено."
    );
  }

  if (
    location.method === "vlm"
  ) {
    return (
      "Область замечания определена "
      + "по изображению листа."
    );
  }

  if (
    location.method === "pdf_text"
  ) {
    return (
      "Область замечания определена "
      + "по координатам текста PDF."
    );
  }

  return (
    "Область замечания определена "
    + "автоматически."
  );
}


function appendDetailField(
  parent,
  label,
  value,
) {
  if (
    value === null
    || value === undefined
    || String(
      value,
    ).trim() === ""
  ) {
    return;
  }

  const field = createElement(
    "span",
    "analysis-result__annotation-detail-field",
  );

  field.append(
    createElement(
      "strong",
      "analysis-result__annotation-detail-label",
      label,
    ),
  );

  field.append(
    createElement(
      "span",
      "analysis-result__annotation-detail-value",
      value,
    ),
  );

  parent.append(
    field,
  );
}


function createFindingDetailControl(
  finding,
  findingIndex,
  dependencies,
) {
  const wrapper = createElement(
    "span",
    "analysis-result__annotation-info",
  );

  const button = createElement(
    "button",
    "analysis-result__annotation-info-button",
    "i",
  );

  button.type = "button";

  button.setAttribute(
    "aria-label",
    (
      `Полное замечание №${findingIndex + 1}`
    ),
  );

  tooltipSequence += 1;

  const tooltipId = (
    `analysis-finding-tooltip-${tooltipSequence}`
  );

  button.setAttribute(
    "aria-describedby",
    tooltipId,
  );

  const tooltip = createElement(
    "span",
    "analysis-result__annotation-detail-tooltip",
  );

  tooltip.id = tooltipId;

  tooltip.setAttribute(
    "role",
    "tooltip",
  );

  tooltip.append(
    createElement(
      "strong",
      "analysis-result__annotation-detail-title",
      (
        `Полное замечание №${findingIndex + 1}`
      ),
    ),
  );

  appendDetailField(
    tooltip,
    "Замечание",
    (
      findingComment(
        finding,
      )
      || "Текст замечания не передан."
    ),
  );

  appendDetailField(
    tooltip,
    "Основание на листе",
    finding.evidence,
  );

  appendDetailField(
    tooltip,
    "Нормативное основание",
    finding.basis,
  );

  appendDetailField(
    tooltip,
    "Нормативные источники",
    sourceListText(
      dependencies.normativeSources(
        finding,
      ),
      "Нормативный источник",
    ),
  );

  appendDetailField(
    tooltip,
    "Требования технического задания",
    sourceListText(
      preferredSourceArray(
        finding.technical_assignment_basis_sources,
        finding.technical_assignment_sources,
      ),
      "Техническое задание",
    ),
  );

  appendDetailField(
    tooltip,
    "Пользовательские требования / документы",
    sourceListText(
      preferredSourceArray(
        finding.user_package_basis_sources,
        finding.user_package_sources,
      ),
      "Пользовательский документ",
    ),
  );

  appendDetailField(
    tooltip,
    "Контекст ПЗ",
    projectContextText(
      finding,
    ),
  );

  appendDetailField(
    tooltip,
    "Рекомендация",
    (
      finding.recommendation
      || finding.recommendation_draft
      || "Не указана."
    ),
  );

  if (
    finding.confidence !== null
    && finding.confidence !== undefined
  ) {
    appendDetailField(
      tooltip,
      "Уверенность",
      finding.confidence,
    );
  }

  appendDetailField(
    tooltip,
    "Локализация",
    localizationText(
      finding,
    ),
  );

  wrapper.append(
    button,
    tooltip,
  );

  return wrapper;
}


function appendNormativeLinks(
  parent,
  finding,
  {
    normativeSources,
    createNormativeCitation,
  },
) {
  const sources = normativeSources(
    finding,
  );

  if (!sources.length) {
    return;
  }

  const sourcesBlock = createElement(
    "div",
    "analysis-result__annotation-sources",
  );

  sourcesBlock.append(
    createElement(
      "span",
      "analysis-result__annotation-source-label",
      "Норматив:",
    ),
  );

  const visibleSources = sources.slice(
    0,
    2,
  );

  visibleSources.forEach(
    (source) => {
      const wrapper = createElement(
        "span",
        "analysis-result__annotation-source",
      );

      const link = createNormativeCitation(
        source,
      );

      const tooltip = createElement(
        "span",
        "analysis-result__annotation-tooltip",
      );

      tooltip.setAttribute(
        "role",
        "tooltip",
      );

      tooltipSequence += 1;

      const tooltipId = (
        `analysis-normative-tooltip-${tooltipSequence}`
      );

      tooltip.id = tooltipId;

      if (
        link.tagName === "A"
      ) {
        link.setAttribute(
          "aria-describedby",
          tooltipId,
        );
      }

      const sourceTitle = [
        (
          source.source_file
          || "Нормативный источник"
        ),
        (
          source.page !== null
          && source.page !== undefined
          ? `стр. ${source.page}`
          : null
        ),
      ]
        .filter(
          Boolean,
        )
        .join(
          ", ",
        );

      tooltip.append(
        createElement(
          "strong",
          "analysis-result__annotation-tooltip-title",
          sourceTitle,
        ),
      );

      tooltip.append(
        createElement(
          "span",
          "analysis-result__annotation-tooltip-text",
          (
            source.text
            || finding.basis
            || (
              "Текст нормативного фрагмента "
              + "не передан."
            )
          ),
        ),
      );

      wrapper.append(
        link,
        tooltip,
      );

      sourcesBlock.append(
        wrapper,
      );
    },
  );

  if (
    sources.length
    > visibleSources.length
  ) {
    sourcesBlock.append(
      createElement(
        "span",
        "analysis-result__annotation-source-more",
        (
          `+${sources.length - visibleSources.length}`
        ),
      ),
    );
  }

  parent.append(
    sourcesBlock,
  );
}


function createCallout(
  finding,
  findingIndex,
  dependencies,
) {
  const callout = createElement(
    "article",
    "analysis-result__annotation",
  );

  callout.tabIndex = 0;

  const findingId = String(
    finding.finding_id
    ?? "",
  );

  if (findingId) {
    callout.dataset.findingId = findingId;
  }

  const header = createElement(
    "div",
    "analysis-result__annotation-header",
  );

  header.append(
    createElement(
      "span",
      "analysis-result__annotation-number",
      findingIndex + 1,
    ),
  );

  header.append(
    createElement(
      "span",
      "analysis-result__annotation-title",
      findingDisplayText(
        finding,
      ),
    ),
  );

  header.append(
    createFindingDetailControl(
      finding,
      findingIndex,
      dependencies,
    ),
  );

  callout.append(
    header,
  );

  appendNormativeLinks(
    callout,
    finding,
    dependencies,
  );

  if (
    !normalizedBoxes(
      finding,
    ).length
  ) {
    callout.append(
      createElement(
        "span",
        "analysis-result__annotation-unlocated",
        (
          "Точное место на листе "
          + "не определено."
        ),
      ),
    );
  }

  return callout;
}


function createBoundingBoxes(
  finding,
  findingIndex,
) {
  return normalizedBoxes(
    finding,
  ).map(
    (box) => {
      const node = createElement(
        "div",
        "analysis-result__bbox",
      );

      node.style.left = (
        `${box.xMin / 10}%`
      );

      node.style.top = (
        `${box.yMin / 10}%`
      );

      node.style.width = (
        `${(
          box.xMax
          - box.xMin
        ) / 10}%`
      );

      node.style.height = (
        `${(
          box.yMax
          - box.yMin
        ) / 10}%`
      );

      if (box.label) {
        node.title = (
          `Область: ${box.label}`
        );
      }

      node.append(
        createElement(
          "span",
          "analysis-result__bbox-number",
          findingIndex + 1,
        ),
      );

      return {
        box,
        node,
      };
    },
  );
}


function clamp(
  value,
  minimum,
  maximum,
) {
  return Math.min(
    Math.max(
      value,
      minimum,
    ),
    maximum,
  );
}


function normalizedBoxToPixels(
  box,
  paneWidth,
  paneHeight,
) {
  return {
    left: (
      box.xMin
      / 1000
      * paneWidth
    ),
    top: (
      box.yMin
      / 1000
      * paneHeight
    ),
    right: (
      box.xMax
      / 1000
      * paneWidth
    ),
    bottom: (
      box.yMax
      / 1000
      * paneHeight
    ),
  };
}


function unionRect(
  rects,
  paneWidth,
  paneHeight,
) {
  if (!rects.length) {
    const centerX = (
      paneWidth
      / 2
    );

    const centerY = (
      paneHeight
      / 2
    );

    return {
      left: centerX,
      top: centerY,
      right: centerX,
      bottom: centerY,
    };
  }

  return {
    left: Math.min(
      ...rects.map(
        (rect) => rect.left,
      ),
    ),
    top: Math.min(
      ...rects.map(
        (rect) => rect.top,
      ),
    ),
    right: Math.max(
      ...rects.map(
        (rect) => rect.right,
      ),
    ),
    bottom: Math.max(
      ...rects.map(
        (rect) => rect.bottom,
      ),
    ),
  };
}


function rectangleArea(
  rect,
) {
  return (
    Math.max(
      0,
      rect.right - rect.left,
    )
    * Math.max(
      0,
      rect.bottom - rect.top,
    )
  );
}


function intersectionArea(
  first,
  second,
) {
  return rectangleArea(
    {
      left: Math.max(
        first.left,
        second.left,
      ),
      top: Math.max(
        first.top,
        second.top,
      ),
      right: Math.min(
        first.right,
        second.right,
      ),
      bottom: Math.min(
        first.bottom,
        second.bottom,
      ),
    },
  );
}


function distanceBetweenCenters(
  first,
  second,
) {
  const firstX = (
    first.left
    + first.right
  ) / 2;

  const firstY = (
    first.top
    + first.bottom
  ) / 2;

  const secondX = (
    second.left
    + second.right
  ) / 2;

  const secondY = (
    second.top
    + second.bottom
  ) / 2;

  return Math.hypot(
    firstX - secondX,
    firstY - secondY,
  );
}


function createCandidateRect(
  left,
  top,
  width,
  height,
  paneWidth,
  paneHeight,
) {
  const safeRight = Math.max(
    CALLOUT_MARGIN_PX,
    paneWidth
    - width
    - CALLOUT_MARGIN_PX,
  );

  const safeBottom = Math.max(
    CALLOUT_MARGIN_PX,
    paneHeight
    - height
    - CALLOUT_MARGIN_PX,
  );

  const clampedLeft = clamp(
    left,
    CALLOUT_MARGIN_PX,
    safeRight,
  );

  const clampedTop = clamp(
    top,
    CALLOUT_MARGIN_PX,
    safeBottom,
  );

  return {
    left: clampedLeft,
    top: clampedTop,
    right: clampedLeft + width,
    bottom: clampedTop + height,
  };
}


function candidateRects(
  anchor,
  width,
  height,
  paneWidth,
  paneHeight,
) {
  const centerX = (
    anchor.left
    + anchor.right
  ) / 2;

  const centerY = (
    anchor.top
    + anchor.bottom
  ) / 2;

  const candidates = [
    [
      anchor.right + CALLOUT_GAP_PX,
      centerY - height / 2,
    ],
    [
      anchor.left
      - width
      - CALLOUT_GAP_PX,
      centerY - height / 2,
    ],
    [
      centerX - width / 2,
      anchor.bottom + CALLOUT_GAP_PX,
    ],
    [
      centerX - width / 2,
      anchor.top
      - height
      - CALLOUT_GAP_PX,
    ],
    [
      anchor.right + CALLOUT_GAP_PX,
      anchor.top
      - height
      - CALLOUT_GAP_PX,
    ],
    [
      anchor.right + CALLOUT_GAP_PX,
      anchor.bottom + CALLOUT_GAP_PX,
    ],
    [
      anchor.left
      - width
      - CALLOUT_GAP_PX,
      anchor.top
      - height
      - CALLOUT_GAP_PX,
    ],
    [
      anchor.left
      - width
      - CALLOUT_GAP_PX,
      anchor.bottom + CALLOUT_GAP_PX,
    ],
  ];

  const verticalOffsets = [
    -1.35,
    -0.7,
    0,
    0.7,
    1.35,
  ];

  verticalOffsets.forEach(
    (factor) => {
      candidates.push(
        [
          paneWidth
          - width
          - CALLOUT_MARGIN_PX,
          (
            centerY
            - height / 2
            + factor
            * (
              height
              + CALLOUT_GAP_PX
            )
          ),
        ],
      );

      candidates.push(
        [
          CALLOUT_MARGIN_PX,
          (
            centerY
            - height / 2
            + factor
            * (
              height
              + CALLOUT_GAP_PX
            )
          ),
        ],
      );
    },
  );

  const horizontalStep = Math.max(
    width + CALLOUT_GAP_PX,
    1,
  );

  const verticalStep = Math.max(
    height + CALLOUT_GAP_PX,
    1,
  );

  for (
    let top = CALLOUT_MARGIN_PX;
    top <= (
      paneHeight
      - height
      - CALLOUT_MARGIN_PX
    );
    top += verticalStep
  ) {
    for (
      let left = CALLOUT_MARGIN_PX;
      left <= (
        paneWidth
        - width
        - CALLOUT_MARGIN_PX
      );
      left += horizontalStep
    ) {
      candidates.push(
        [
          left,
          top,
        ],
      );
    }
  }

  const unique = new Map();

  candidates.forEach(
    ([
      left,
      top,
    ]) => {
      const rect = createCandidateRect(
        left,
        top,
        width,
        height,
        paneWidth,
        paneHeight,
      );

      const key = (
        `${Math.round(rect.left)}:`
        + `${Math.round(rect.top)}`
      );

      if (!unique.has(
        key,
      )) {
        unique.set(
          key,
          rect,
        );
      }
    },
  );

  return Array.from(
    unique.values(),
  );
}


function candidateScore(
  candidate,
  anchor,
  occupiedCards,
  allRegions,
) {
  const cardOverlap = occupiedCards.reduce(
    (
      total,
      occupied,
    ) => (
      total
      + intersectionArea(
        candidate,
        occupied,
      )
    ),
    0,
  );

  const regionOverlap = allRegions.reduce(
    (
      total,
      region,
    ) => (
      total
      + intersectionArea(
        candidate,
        region,
      )
    ),
    0,
  );

  const distance = distanceBetweenCenters(
    candidate,
    anchor,
  );

  return (
    cardOverlap
    * CARD_OVERLAP_WEIGHT
    + regionOverlap
    * REGION_OVERLAP_WEIGHT
    + distance
    * DISTANCE_WEIGHT
  );
}


function layoutOverlayAnnotations(
  imagePane,
  items,
) {
  const paneWidth = Math.max(
    imagePane.clientWidth,
    1,
  );

  const paneHeight = Math.max(
    imagePane.clientHeight,
    1,
  );

  const allRegions = items.flatMap(
    (item) => (
      item.bboxEntries.map(
        ({
          box,
        }) => (
          normalizedBoxToPixels(
            box,
            paneWidth,
            paneHeight,
          )
        ),
      )
    ),
  );

  const occupiedCards = [];

  const orderedItems = items
    .map(
      (item) => {
        const regionRects = (
          item.bboxEntries.map(
            ({
              box,
            }) => (
              normalizedBoxToPixels(
                box,
                paneWidth,
                paneHeight,
              )
            ),
          )
        );

        const anchor = unionRect(
          regionRects,
          paneWidth,
          paneHeight,
        );

        return {
          ...item,
          anchor,
        };
      },
    )
    .sort(
      (
        left,
        right,
      ) => (
        left.anchor.top
        - right.anchor.top
        || left.anchor.left
        - right.anchor.left
        || left.findingIndex
        - right.findingIndex
      ),
    );

  orderedItems.forEach(
    (item) => {
      const calloutWidth = Math.max(
        item.callout.offsetWidth,
        1,
      );

      const calloutHeight = Math.max(
        item.callout.offsetHeight,
        1,
      );

      const candidates = candidateRects(
        item.anchor,
        calloutWidth,
        calloutHeight,
        paneWidth,
        paneHeight,
      );

      const best = candidates.reduce(
        (
          currentBest,
          candidate,
        ) => {
          const score = candidateScore(
            candidate,
            item.anchor,
            occupiedCards,
            allRegions,
          );

          if (
            currentBest === null
            || score < currentBest.score
          ) {
            return {
              rect: candidate,
              score,
            };
          }

          return currentBest;
        },
        null,
      );

      if (!best) {
        return;
      }

      item.callout.style.left = (
        `${best.rect.left}px`
      );

      item.callout.style.top = (
        `${best.rect.top}px`
      );

      item.callout.style.visibility = (
        "visible"
      );

      occupiedCards.push(
        best.rect,
      );
    },
  );
}


function nearestConnectorPoints(
  bboxRect,
  calloutRect,
  paneRect,
) {
  const box = {
    left: (
      bboxRect.left
      - paneRect.left
    ),
    top: (
      bboxRect.top
      - paneRect.top
    ),
    right: (
      bboxRect.right
      - paneRect.left
    ),
    bottom: (
      bboxRect.bottom
      - paneRect.top
    ),
  };

  const callout = {
    left: (
      calloutRect.left
      - paneRect.left
    ),
    top: (
      calloutRect.top
      - paneRect.top
    ),
    right: (
      calloutRect.right
      - paneRect.left
    ),
    bottom: (
      calloutRect.bottom
      - paneRect.top
    ),
  };

  const boxCenterX = (
    box.left
    + box.right
  ) / 2;

  const boxCenterY = (
    box.top
    + box.bottom
  ) / 2;

  const calloutCenterX = (
    callout.left
    + callout.right
  ) / 2;

  const calloutCenterY = (
    callout.top
    + callout.bottom
  ) / 2;

  const horizontalDelta = (
    calloutCenterX
    - boxCenterX
  );

  const verticalDelta = (
    calloutCenterY
    - boxCenterY
  );

  if (
    Math.abs(
      horizontalDelta,
    )
    >= Math.abs(
      verticalDelta,
    )
  ) {
    return {
      start: {
        x: (
          horizontalDelta >= 0
            ? box.right
            : box.left
        ),
        y: boxCenterY,
      },
      end: {
        x: (
          horizontalDelta >= 0
            ? callout.left
            : callout.right
        ),
        y: calloutCenterY,
      },
      axis: "horizontal",
    };
  }

  return {
    start: {
      x: boxCenterX,
      y: (
        verticalDelta >= 0
          ? box.bottom
          : box.top
      ),
    },
    end: {
      x: calloutCenterX,
      y: (
        verticalDelta >= 0
          ? callout.top
          : callout.bottom
      ),
    },
    axis: "vertical",
  };
}


function drawConnector(
  imagePane,
  svg,
  polyline,
  bboxNode,
  callout,
) {
  if (
    !bboxNode
    || !callout
  ) {
    polyline.setAttribute(
      "points",
      "",
    );

    return;
  }

  const paneRect = (
    imagePane.getBoundingClientRect()
  );

  const bboxRect = (
    bboxNode.getBoundingClientRect()
  );

  const calloutRect = (
    callout.getBoundingClientRect()
  );

  const width = Math.max(
    imagePane.clientWidth,
    1,
  );

  const height = Math.max(
    imagePane.clientHeight,
    1,
  );

  svg.setAttribute(
    "viewBox",
    `0 0 ${width} ${height}`,
  );

  const points = nearestConnectorPoints(
    bboxRect,
    calloutRect,
    paneRect,
  );

  if (
    points.axis === "horizontal"
  ) {
    const elbowX = (
      points.start.x
      + (
        points.end.x
        - points.start.x
      ) / 2
    );

    polyline.setAttribute(
      "points",
      [
        (
          `${points.start.x},`
          + `${points.start.y}`
        ),
        (
          `${elbowX},`
          + `${points.start.y}`
        ),
        (
          `${elbowX},`
          + `${points.end.y}`
        ),
        (
          `${points.end.x},`
          + `${points.end.y}`
        ),
      ].join(
        " ",
      ),
    );

    return;
  }

  const elbowY = (
    points.start.y
    + (
      points.end.y
      - points.start.y
    ) / 2
  );

  polyline.setAttribute(
    "points",
    [
      (
        `${points.start.x},`
        + `${points.start.y}`
      ),
      (
        `${points.start.x},`
        + `${elbowY}`
      ),
      (
        `${points.end.x},`
        + `${elbowY}`
      ),
      (
        `${points.end.x},`
        + `${points.end.y}`
      ),
    ].join(
      " ",
    ),
  );
}


function setFindingActive(
  item,
  active,
) {
  item.callout.classList.toggle(
    "analysis-result__annotation--active",
    active,
  );
  item.callout.classList.toggle(
    "analysis-result__group-member--active",
    active,
  );

  item.bboxEntries.forEach(
    ({
      node,
    }) => {
      node.classList.toggle(
        "analysis-result__bbox--active",
        active,
      );
    },
  );

  item.connectorEntries.forEach(
    ({
      polyline,
    }) => {
      polyline.classList.toggle(
        "analysis-result__connector--active",
        active,
      );
    },
  );
}


function bindFindingInteractions(
  item,
) {
  const activate = (event) => {
    if (event?.target?.closest?.(".analysis-result__hypotheses")) {
      setFindingActive(item, false);
      return;
    }
    setFindingActive(
      item,
      true,
    );
  };

  const deactivate = () => {
    setFindingActive(
      item,
      false,
    );
  };

  item.callout.addEventListener(
    "pointerenter",
    activate,
  );

  item.callout.addEventListener(
    "pointerleave",
    deactivate,
  );

  item.callout.addEventListener(
    "focusin",
    activate,
  );

  item.callout.addEventListener(
    "focusout",
    (event) => {
      if (
        event.relatedTarget
        && item.callout.contains(
          event.relatedTarget,
        )
      ) {
        return;
      }

      deactivate();
    },
  );

  if (item.callout.classList.contains("analysis-result__annotation")) {
    item.callout.querySelectorAll(".analysis-result__hypotheses")
      .forEach((details) => {
        details.addEventListener("pointerenter", deactivate);
        details.addEventListener("focusin", deactivate);
      });
    item.callout.querySelector(".analysis-result__annotation-header")
      ?.addEventListener("pointerenter", activate);
  }

  item.bboxEntries.forEach(
    ({
      node,
    }) => {
      node.addEventListener(
        "pointerenter",
        activate,
      );

      node.addEventListener(
        "pointerleave",
        deactivate,
      );
    },
  );
}


function findingObjectAnchor(finding) {
  const explicit = String(finding.object_ref ?? "").trim();
  return explicit.toLocaleLowerCase("ru-RU");
}


function boxesAreNear(left, right) {
  return left.some((a) => right.some((b) => (
    Math.max(a.xMin - b.xMax, b.xMin - a.xMax, 0) <= GROUP_AREA_GAP
    && Math.max(a.yMin - b.yMax, b.yMin - a.yMax, 0) <= GROUP_AREA_GAP
  )));
}


function findingGroups(findings) {
  const groups = [];
  findings.forEach((entry) => {
    const anchor = findingObjectAnchor(entry.finding);
    const boxes = normalizedBoxes(entry.finding);
    const existing = anchor && boxes.length
      ? groups.find((group) => (
        group.anchor === anchor
        && group.members.every((member) => boxesAreNear(
          boxes,
          normalizedBoxes(member.finding),
        ))
      ))
      : null;
    if (existing) {
      existing.members.push(entry);
    } else {
      groups.push({ anchor, members: [entry], hypotheses: [] });
    }
  });
  return groups;
}


function createHypothesisDetails(hypotheses) {
  const details = createElement("details", "analysis-result__hypotheses");
  details.append(createElement(
    "summary",
    "analysis-result__hypotheses-summary",
    `Неподтверждённые гипотезы (${hypotheses.length})`,
  ));
  hypotheses.forEach((finding) => {
    const item = createElement("div", "analysis-result__hypothesis");
    item.append(createElement("strong", "", findingComment(finding)));
    if (finding.evidence) {
      item.append(createElement("p", "", finding.evidence));
    }
    item.append(createElement(
      "small",
      "",
      `Исходные области VLM, не подтверждены: ${JSON.stringify(finding.visual_regions ?? [])}`,
    ));
    details.append(item);
  });
  return details;
}


function createGroupedCallout(group, dependencies) {
  const callout = createElement("article", "analysis-result__annotation analysis-result__annotation--group");
  callout.tabIndex = 0;
  callout.append(createElement(
    "strong",
    "analysis-result__group-title",
    `${String(group.members[0].finding.object_ref).trim()}: ${group.members.length} замечания`,
  ));
  const rows = [];
  group.members.forEach(({ finding, findingIndex }) => {
    const row = createElement("div", "analysis-result__group-member");
    row.tabIndex = 0;
    row.dataset.findingId = String(finding.finding_id ?? "");
    row.append(createElement("span", "analysis-result__annotation-number", findingIndex + 1));
    row.append(createElement("span", "analysis-result__group-member-text", findingDisplayText(finding)));
    row.append(createFindingDetailControl(finding, findingIndex, dependencies));
    appendNormativeLinks(row, finding, dependencies);
    callout.append(row);
    rows.push(row);
  });
  if (group.hypotheses.length) {
    callout.append(createHypothesisDetails(group.hypotheses));
  }
  return { callout, rows };
}


function appendPageVisualization(
  page,
  payload,
  parent,
  dependencies,
  pageIndex,
) {
  const pageNumber = normalizedPage(
    page.page_number,
  );

  if (pageNumber === null) {
    return;
  }

  const section = createElement(
    "section",
    "analysis-result__page-visualization",
  );

  section.append(
    createElement(
      "h4",
      "analysis-result__page-title",
      `Лист/страница ${pageNumber}`,
    ),
  );

  if (
    page.localization_warning
  ) {
    section.append(
      createElement(
        "p",
        (
          "analysis-result__"
          + "visualization-warning"
        ),
        page.localization_warning,
      ),
    );
  }

  const stage = createElement(
    "div",
    "analysis-result__page-stage",
  );

  const imagePane = createElement(
    "div",
    "analysis-result__page-image-pane",
  );

  const image = createElement(
    "img",
    "analysis-result__page-image",
  );

  image.alt = (
    `Страница ${pageNumber} исходного PDF`
  );

  image.decoding = "async";

  if (pageIndex > 0) {
    image.loading = "lazy";
  }

  image.src = (
    "data:image/png;base64,"
    + String(
      page.image_base64
      || "",
    )
  );

  const svg = (
    document.createElementNS(
      "http://www.w3.org/2000/svg",
      "svg",
    )
  );

  svg.classList.add(
    "analysis-result__connectors",
  );

  svg.setAttribute(
    "aria-hidden",
    "true",
  );

  const annotationOverlay = createElement(
    "div",
    "analysis-result__annotation-list",
  );

  const findings = findingsForPage(
    payload,
    page,
  );

  const groups = findingGroups(findings);
  const hypotheses = (Array.isArray(payload.findings) ? payload.findings : [])
    .filter((finding) => (
      finding.status === "hypothesis"
      && normalizedPage(finding.page ?? finding.page_number) === pageNumber
    ));
  const unattachedHypotheses = [];
  hypotheses.forEach((hypothesis) => {
    const anchor = findingObjectAnchor(hypothesis);
    const matches = anchor
      ? groups.filter((group) => group.anchor === anchor)
      : [];
    if (matches.length === 1) {
      matches[0].hypotheses.push(hypothesis);
    } else {
      unattachedHypotheses.push(hypothesis);
    }
  });

  const items = [];

  groups.forEach((group) => {
      const isGrouped = group.members.length > 1;
      const groupControl = isGrouped
        ? createGroupedCallout(group, dependencies)
        : null;
      const { finding, findingIndex } = group.members[0];
      const callout = groupControl?.callout
        ?? createCallout(finding, findingIndex, dependencies);
      if (!isGrouped && group.hypotheses.length) {
        callout.append(createHypothesisDetails(group.hypotheses));
      }

      const bboxEntries = [];
      const connectorEntries = [];
      group.members.forEach((member, memberIndex) => {
        const memberBoxes = createBoundingBoxes(
          member.finding,
          member.findingIndex,
        );
        const memberConnectors = memberBoxes.map(({ node }) => {
          imagePane.append(node);
          const polyline = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "polyline",
          );
          polyline.classList.add("analysis-result__connector");
          svg.append(polyline);
          return { bboxNode: node, polyline };
        });
        bboxEntries.push(...memberBoxes);
        connectorEntries.push(...memberConnectors);
        if (isGrouped) {
          bindFindingInteractions({
            callout: groupControl.rows[memberIndex],
            bboxEntries: memberBoxes,
            connectorEntries: memberConnectors,
          });
        }
      });

      callout.style.left = "0";
      callout.style.top = "0";
      callout.style.visibility = (
        "hidden"
      );

      annotationOverlay.append(
        callout,
      );

      const item = {
        finding,
        findingIndex,
        bboxEntries,
        callout,
        connectorEntries,
      };

      if (!isGrouped) {
        bindFindingInteractions(item);
      }

      items.push(
        item,
      );
  });

  if (!findings.length) {
    annotationOverlay.append(
      createElement(
        "p",
        "analysis-result__page-empty",
        (
          "На этой странице замечания "
          + "не сформированы."
        ),
      ),
    );
  }

  imagePane.append(
    image,
    svg,
    annotationOverlay,
  );

  stage.append(
    imagePane,
  );

  section.append(
    stage,
  );

  if (unattachedHypotheses.length) {
    section.append(createHypothesisDetails(unattachedHypotheses));
  }

  parent.append(
    section,
  );

  const redraw = () => {
    if (
      imagePane.clientWidth <= 0
      || imagePane.clientHeight <= 0
    ) {
      return;
    }

    layoutOverlayAnnotations(
      imagePane,
      items,
    );

    items.forEach(
      (item) => {
        item.connectorEntries.forEach(
          ({
            bboxNode,
            polyline,
          }) => {
            drawConnector(
              imagePane,
              svg,
              polyline,
              bboxNode,
              item.callout,
            );
          },
        );
      },
    );
  };

  const scheduleRedraw = () => {
    window.requestAnimationFrame(
      redraw,
    );
  };

  annotationOverlay.addEventListener("toggle", scheduleRedraw, true);

  image.addEventListener(
    "load",
    scheduleRedraw,
    {
      once: true,
    },
  );

  if (image.complete) {
    scheduleRedraw();
  }

  if (
    typeof ResizeObserver
    !== "undefined"
  ) {
    const observer = (
      new ResizeObserver(
        scheduleRedraw,
      )
    );

    observer.observe(
      imagePane,
    );

  } else {
    window.addEventListener(
      "resize",
      scheduleRedraw,
      {
        passive: true,
      },
    );
  }
}


/**
 * Добавляет visual pages перед текстовой частью результата.
 */
export function appendAnalysisVisualization(
  payload,
  visualization,
  parent,
  dependencies,
) {
  const hasPdf = (
    payload.source_mode === "pdf_only"
    || payload.source_mode === "pdf_cad"
  );

  if (!hasPdf) {
    return;
  }

  const section = createElement(
    "section",
    "analysis-result__visualization",
  );

  section.append(
    createElement(
      "h3",
      "analysis-result__section-title",
      "Визуализация замечаний",
    ),
  );

  const pages = (
    Array.isArray(
      visualization?.pages,
    )
      ? visualization.pages
      : []
  );

  if (!pages.length) {
    section.append(
      createElement(
        "p",
        (
          "analysis-result__"
          + "visualization-warning"
        ),
        (
          visualization?.error
          || "PDF-preview недоступен. "
          + (
            "Текстовые замечания "
            + "приведены ниже."
          )
        ),
      ),
    );

    parent.append(
      section,
    );

    return;
  }

  pages
    .slice()
    .sort(
      (
        left,
        right,
      ) => (
        Number(
          left.page_number,
        )
        - Number(
          right.page_number,
        )
      ),
    )
    .forEach(
      (
        page,
        pageIndex,
      ) => {
        appendPageVisualization(
          page,
          payload,
          section,
          dependencies,
          pageIndex,
        );
      },
    );

  parent.append(
    section,
  );
}
