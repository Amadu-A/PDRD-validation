// frontend/src/js/features/analysis/visualization.js

/**
 * Визуализация findings поверх rendered PDF pages.
 *
 * BBox приходит в нормализованных координатах 0..1000.
 * Текст и source data вставляются только через textContent.
 */

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


function normalizedBox(
  finding,
) {
  const location = finding?.location;

  if (
    location?.status !== "located"
    || !location.bbox
  ) {
    return null;
  }

  const box = {
    xMin: Number(
      location.bbox.x_min,
    ),
    yMin: Number(
      location.bbox.y_min,
    ),
    xMax: Number(
      location.bbox.x_max,
    ),
    yMax: Number(
      location.bbox.y_max,
    ),
  };

  const values = Object.values(
    box,
  );

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


function findingsForPage(
  payload,
  pageNumber,
) {
  if (!Array.isArray(
    payload.findings,
  )) {
    return [];
  }

  return payload.findings.filter(
    (finding) => (
      normalizedPage(
        finding.page
        ?? finding.page_number,
      ) === pageNumber
    ),
  );
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

  sources.forEach(
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
        source.source_file
          || "Нормативный источник",
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
            || "Текст нормативного фрагмента не передан."
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
      (
        finding.comment
        || finding.message
        || finding.issue_text
        || "Текст замечания не передан."
      ),
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

  if (!normalizedBox(
    finding,
  )) {
    callout.append(
      createElement(
        "span",
        "analysis-result__annotation-unlocated",
        "Точное место на листе не определено.",
      ),
    );
  }

  return callout;
}


function createBoundingBox(
  finding,
  findingIndex,
) {
  const box = normalizedBox(
    finding,
  );

  if (!box) {
    return null;
  }

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
    `${(box.xMax - box.xMin) / 10}%`
  );
  node.style.height = (
    `${(box.yMax - box.yMin) / 10}%`
  );

  node.append(
    createElement(
      "span",
      "analysis-result__bbox-number",
      findingIndex + 1,
    ),
  );

  return node;
}


function drawConnector(
  stage,
  svg,
  polyline,
  bboxNode,
  callout,
) {
  if (
    !bboxNode
    || window.matchMedia(
      "(max-width: 900px)",
    ).matches
  ) {
    polyline.setAttribute(
      "points",
      "",
    );

    return;
  }

  const stageRect = stage.getBoundingClientRect();
  const bboxRect = bboxNode.getBoundingClientRect();
  const calloutRect = callout.getBoundingClientRect();

  const width = Math.max(
    stage.clientWidth,
    1,
  );

  const height = Math.max(
    stage.clientHeight,
    1,
  );

  svg.setAttribute(
    "viewBox",
    `0 0 ${width} ${height}`,
  );

  const x1 = (
    bboxRect.right
    - stageRect.left
  );

  const y1 = (
    bboxRect.top
    - stageRect.top
    + bboxRect.height / 2
  );

  const x2 = (
    calloutRect.left
    - stageRect.left
  );

  const y2 = (
    calloutRect.top
    - stageRect.top
    + Math.min(
      calloutRect.height / 2,
      36,
    )
  );

  const elbow = (
    x1
    + Math.max(
      12,
      (x2 - x1) * 0.45,
    )
  );

  polyline.setAttribute(
    "points",
    [
      `${x1},${y1}`,
      `${elbow},${y1}`,
      `${elbow},${y2}`,
      `${x2},${y2}`,
    ].join(
      " ",
    ),
  );
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

  imagePane.append(
    image,
  );

  const calloutPane = createElement(
    "div",
    "analysis-result__annotation-list",
  );

  const svg = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "svg",
  );

  svg.classList.add(
    "analysis-result__connectors",
  );

  svg.setAttribute(
    "aria-hidden",
    "true",
  );

  const findings = findingsForPage(
    payload,
    pageNumber,
  );

  const connectorPairs = [];

  findings.forEach(
    (
      finding,
      findingIndex,
    ) => {
      const bboxNode = createBoundingBox(
        finding,
        findingIndex,
      );

      if (bboxNode) {
        imagePane.append(
          bboxNode,
        );
      }

      const callout = createCallout(
        finding,
        findingIndex,
        dependencies,
      );

      calloutPane.append(
        callout,
      );

      if (bboxNode) {
        const polyline = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "polyline",
        );

        polyline.classList.add(
          "analysis-result__connector",
        );

        svg.append(
          polyline,
        );

        connectorPairs.push(
          {
            bboxNode,
            callout,
            polyline,
          },
        );
      }
    },
  );

  if (!findings.length) {
    calloutPane.append(
      createElement(
        "p",
        "analysis-result__page-empty",
        "На этой странице замечания не сформированы.",
      ),
    );
  }

  stage.append(
    imagePane,
    calloutPane,
    svg,
  );

  section.append(
    stage,
  );

  parent.append(
    section,
  );

  const redraw = () => {
    connectorPairs.forEach(
      ({
        bboxNode,
        callout,
        polyline,
      }) => {
        drawConnector(
          stage,
          svg,
          polyline,
          bboxNode,
          callout,
        );
      },
    );
  };

  image.addEventListener(
    "load",
    redraw,
    {
      once: true,
    },
  );

  if (
    typeof ResizeObserver !== "undefined"
  ) {
    const observer = new ResizeObserver(
      redraw,
    );

    observer.observe(
      stage,
    );

  } else {
    window.addEventListener(
      "resize",
      redraw,
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
        "analysis-result__visualization-warning",
        (
          visualization?.error
          || "PDF-preview недоступен. "
          + "Текстовые замечания приведены ниже."
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
