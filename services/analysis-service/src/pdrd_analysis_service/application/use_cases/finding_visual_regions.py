# services/analysis-service/src/pdrd_analysis_service/application/use_cases/finding_visual_regions.py

"""Pure helpers visual provenance generated findings."""

from typing import Any

from pdrd_analysis_service.domain.analysis import (
    FindingVisualRegion,
)


def parse_finding_visual_regions(
    value: Any,
    *,
    limit: int = 4,
) -> tuple[
    FindingVisualRegion,
    ...,
]:
    """Безопасно преобразует VLM visual_regions в typed domain regions."""
    if not isinstance(
        value,
        list,
    ):
        return ()

    regions: list[FindingVisualRegion,] = []

    seen: set[
        tuple[
            int,
            int,
            int,
            int,
        ]
    ] = set()

    for raw_region in value:
        if len(regions) >= limit:
            break

        if not isinstance(
            raw_region,
            dict,
        ):
            continue

        raw_coordinates = (
            raw_region.get(
                "x_min",
            ),
            raw_region.get(
                "y_min",
            ),
            raw_region.get(
                "x_max",
            ),
            raw_region.get(
                "y_max",
            ),
        )

        if any(
            isinstance(
                coordinate,
                bool,
            )
            for coordinate in raw_coordinates
        ):
            continue

        try:
            coordinates = tuple(
                int(
                    coordinate,
                )
                for coordinate in raw_coordinates
            )

            raw_confidence = raw_region.get(
                "confidence",
                0.0,
            )

            if isinstance(
                raw_confidence,
                bool,
            ):
                continue

            confidence = float(
                raw_confidence,
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        (
            x_min,
            y_min,
            x_max,
            y_max,
        ) = coordinates

        identity = (
            x_min,
            y_min,
            x_max,
            y_max,
        )

        if identity in seen:
            continue

        label = str(
            raw_region.get(
                "label",
                "",
            )
        ).strip()[:120]

        try:
            region = FindingVisualRegion(
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
                confidence=confidence,
                label=label,
            )

        except ValueError:
            continue

        seen.add(
            identity,
        )

        regions.append(
            region,
        )

    return tuple(
        regions,
    )
