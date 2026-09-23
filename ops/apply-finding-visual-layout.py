# ops/apply-finding-visual-layout.py

"""Применяет Stage 5 collision-aware layout к PDF annotator."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[1]
)

TARGET = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "infrastructure"
    / "pdf"
    / "annotator.py"
)


def _replace_once(
    source: str,
    old: str,
    new: str,
    *,
    label: str,
) -> str:
    """Заменяет ровно один ожидаемый фрагмент."""
    count = source.count(
        old,
    )

    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one match, found {count}.",
        )

    return source.replace(
        old,
        new,
        1,
    )


def main() -> None:
    """Добавляет source-text collision penalty в текущий annotator."""
    source = TARGET.read_text(
        encoding="utf-8",
    )

    source = _replace_once(
        source,
        """    _CARD_OVERLAP_WEIGHT = 12.0
    _REGION_OVERLAP_WEIGHT = 5.0
    _DISTANCE_WEIGHT = 0.015
""",
        """    _CARD_OVERLAP_WEIGHT = 12.0
    _REGION_OVERLAP_WEIGHT = 5.0
    _CONTENT_OVERLAP_WEIGHT = 18.0
    _DISTANCE_WEIGHT = 0.015
""",
        label="weights",
    )

    source = _replace_once(
        source,
        """                occupied_cards: dict[
                    int,
                    list[fitz.Rect],
                ] = {}

                for annotation in annotations:
""",
        """                text_regions_by_page = self._all_page_text_regions(
                    document=document,
                    original_page_count=original_page_count,
                    annotations=annotations,
                )

                occupied_cards: dict[
                    int,
                    list[fitz.Rect],
                ] = {}

                for annotation in annotations:
""",
        label="text region initialization",
    )

    source = _replace_once(
        source,
        """                        all_regions=(
                            all_regions_by_page.get(
                                annotation.page_number,
                                (),
                            )
                        ),
                    )
""",
        """                        all_regions=(
                            all_regions_by_page.get(
                                annotation.page_number,
                                (),
                            )
                        ),
                        text_regions=(
                            text_regions_by_page.get(
                                annotation.page_number,
                                (),
                            )
                        ),
                    )
""",
        label="annotation text regions",
    )

    marker = """    def _add_finding_annotation(
        self,
"""

    if (
        source.count(
            marker,
        )
        != 1
    ):
        raise RuntimeError(
            "add finding annotation marker not found exactly once.",
        )

    helper = """    def _all_page_text_regions(
        self,
        *,
        document: fitz.Document,
        original_page_count: int,
        annotations: tuple[
            PdfFindingAnnotation,
            ...,
        ],
    ) -> dict[
        int,
        tuple[
            fitz.Rect,
            ...,
        ],
    ]:
        \"\"\"Собирает исходные text blocks для collision-aware card layout.\"\"\"
        page_numbers = {
            annotation.page_number
            for annotation in annotations
        }

        result: dict[
            int,
            tuple[
                fitz.Rect,
                ...,
            ],
        ] = {}

        for page_number in page_numbers:
            page = self._annotation_page(
                document=document,
                original_page_count=original_page_count,
                page_number=page_number,
            )

            regions: list[
                fitz.Rect,
            ] = []

            for block in page.get_text(
                "blocks",
            ):
                if (
                    not isinstance(
                        block,
                        tuple,
                    )
                    or len(
                        block,
                    )
                    < 5
                ):
                    continue

                text = str(
                    block[4],
                ).strip()

                if not text:
                    continue

                rect = fitz.Rect(
                    float(
                        block[0],
                    ),
                    float(
                        block[1],
                    ),
                    float(
                        block[2],
                    ),
                    float(
                        block[3],
                    ),
                )

                if page.rotation:
                    rect = (
                        rect
                        * page.rotation_matrix
                    )

                rect = (
                    rect
                    & page.rect
                )

                if (
                    rect.is_empty
                    or rect.is_infinite
                ):
                    continue

                regions.append(
                    rect,
                )

            result[
                page_number
            ] = tuple(
                regions,
            )

        return result

"""

    source = source.replace(
        marker,
        helper + marker,
        1,
    )

    source = _replace_once(
        source,
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> None:
        \"\"\"Добавляет bbox, connector, card и native info одного finding.\"\"\"
""",
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
        text_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> None:
        \"\"\"Добавляет bbox, connector, card и native info одного finding.\"\"\"
""",
        label="finding annotation signature",
    )

    source = _replace_once(
        source,
        """            occupied_cards=occupied_cards,
            all_regions=all_regions,
        )
""",
        """            occupied_cards=occupied_cards,
            all_regions=all_regions,
            text_regions=text_regions,
        )
""",
        label="place card call",
    )

    source = _replace_once(
        source,
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> fitz.Rect:
        \"\"\"Выбирает свободное место card рядом с finding bbox.\"\"\"
""",
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
        text_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> fitz.Rect:
        \"\"\"Выбирает свободное место card рядом с finding bbox.\"\"\"
""",
        label="place card signature",
    )

    source = _replace_once(
        source,
        """                occupied_cards=(occupied_cards),
                all_regions=all_regions,
            ),
""",
        """                occupied_cards=occupied_cards,
                all_regions=all_regions,
                text_regions=text_regions,
            ),
""",
        label="candidate score call",
    )

    source = _replace_once(
        source,
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> float:
        \"\"\"Штрафует card-card/card-region overlap и большую дистанцию.\"\"\"
""",
        """        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
        text_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> float:
        \"\"\"Штрафует overlap с cards/findings/source text и дистанцию.\"\"\"
""",
        label="candidate score signature",
    )

    source = _replace_once(
        source,
        """        distance = self._distance_between_centers(
            candidate,
            anchor,
        )

        return (
            card_overlap * self._CARD_OVERLAP_WEIGHT
            + region_overlap * self._REGION_OVERLAP_WEIGHT
            + distance * self._DISTANCE_WEIGHT
        )
""",
        """        content_overlap = sum(
            self._intersection_area(
                candidate,
                region,
            )
            for region in text_regions
        )

        distance = self._distance_between_centers(
            candidate,
            anchor,
        )

        return (
            card_overlap * self._CARD_OVERLAP_WEIGHT
            + region_overlap * self._REGION_OVERLAP_WEIGHT
            + content_overlap * self._CONTENT_OVERLAP_WEIGHT
            + distance * self._DISTANCE_WEIGHT
        )
""",
        label="candidate score content overlap",
    )

    TARGET.write_text(
        source,
        encoding="utf-8",
    )

    print(
        f"updated: {TARGET.relative_to(ROOT)}",
    )


if __name__ == "__main__":
    main()
