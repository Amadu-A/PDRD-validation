# services/document-service/src/pdrd_document_service/infrastructure/cad/parser.py

"""Машинный анализ DXF через ezdxf."""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import recover

from pdrd_document_service.application.ports.cad import (
    CadProcessingError,
)

type Point = tuple[float, float]
type Segment = tuple[Point, Point]


_INSERT_UNITS = {
    0: "unitless",
    1: "inches",
    2: "feet",
    3: "miles",
    4: "millimeters",
    5: "centimeters",
    6: "meters",
    7: "kilometers",
    8: "microinches",
    9: "mils",
    10: "yards",
    11: "angstroms",
    12: "nanometers",
    13: "microns",
    14: "decimeters",
}


@dataclass(frozen=True, slots=True)
class ParsedCad:
    """Результат чтения DXF до raster rendering."""

    document: ezdxf.document.Drawing
    selected_layout: Any

    warnings: tuple[str, ...]
    machine_data: dict[str, Any]


class EzdxfCadParser:
    """Извлекает машинные данные из DXF."""

    def __init__(
        self,
        *,
        text_sample_limit: int,
        block_sample_limit: int,
        dangling_sample_limit: int,
        connectivity_tolerance: float,
        virtual_insert_depth: int,
    ) -> None:
        """Сохраняет ограничения машинного представления."""
        self._text_sample_limit = text_sample_limit
        self._block_sample_limit = block_sample_limit
        self._dangling_sample_limit = dangling_sample_limit

        self._connectivity_tolerance = connectivity_tolerance

        self._virtual_insert_depth = virtual_insert_depth

    def parse(
        self,
        dxf_path: Path,
    ) -> ParsedCad:
        """Читает DXF и строит компактное машинное представление."""
        document, load_warnings = self._load_dxf(
            dxf_path,
        )

        (
            selected_layout,
            layout_warnings,
            layout_stats,
        ) = self._select_layout(
            document,
        )

        machine_data = self._collect_machine_data(
            document=document,
            selected_layout=selected_layout,
            layout_stats=layout_stats,
        )

        return ParsedCad(
            document=document,
            selected_layout=selected_layout,
            warnings=(
                *load_warnings,
                *layout_warnings,
            ),
            machine_data=machine_data,
        )

    @staticmethod
    def _load_dxf(
        dxf_path: Path,
    ) -> tuple[
        ezdxf.document.Drawing,
        tuple[str, ...],
    ]:
        """Читает DXF с recovery повреждённых структур."""
        warnings: list[str] = []

        try:
            document, auditor = recover.readfile(
                dxf_path,
            )
        except (
            OSError,
            ezdxf.DXFError,
        ) as error:
            raise CadProcessingError(
                f"Не удалось прочитать DXF. Причина: {error}",
            ) from error

        if auditor.has_errors:
            warnings.append(
                "DXF содержит структурные ошибки; ezdxf выполнил recovery.",
            )

        if auditor.has_fixes:
            warnings.append(
                "При чтении DXF автоматически применены исправления.",
            )

        return (
            document,
            tuple(warnings),
        )

    @staticmethod
    def _layout_entity_count(
        layout: Any,
    ) -> int:
        """Безопасно считает количество entities layout."""
        try:
            return len(
                layout,
            )
        except TypeError:
            return sum(1 for _ in layout)

    def _select_layout(
        self,
        document: ezdxf.document.Drawing,
    ) -> tuple[
        Any,
        tuple[str, ...],
        list[dict[str, Any]],
    ]:
        """Выбирает paper-space или model-space для обработки."""
        warnings: list[str] = []
        layout_stats: list[dict[str, Any]] = []

        paper_layouts: list[Any] = []

        for name in document.layout_names():
            layout = document.layouts.get(
                name,
            )

            entity_count = self._layout_entity_count(
                layout,
            )

            is_modelspace = name.lower() == "model"

            layout_stats.append(
                {
                    "name": name,
                    "is_modelspace": is_modelspace,
                    "entity_count": entity_count,
                }
            )

            if not is_modelspace and entity_count > 0:
                paper_layouts.append(
                    layout,
                )

        if len(paper_layouts) == 1:
            return (
                paper_layouts[0],
                (),
                layout_stats,
            )

        if len(paper_layouts) > 1:
            warnings.append(
                "В CAD-файле обнаружено несколько непустых "
                "paper-space layouts. Для MVP выбран первый; "
                "рекомендуется передавать файл одного листа.",
            )

            return (
                paper_layouts[0],
                tuple(warnings),
                layout_stats,
            )

        return (
            document.modelspace(),
            (),
            layout_stats,
        )

    @staticmethod
    def _xy(
        value: Any,
    ) -> Point:
        """Преобразует координату ezdxf в нормализованный Point."""
        return (
            round(
                float(value[0]),
                6,
            ),
            round(
                float(value[1]),
                6,
            ),
        )

    def _entity_segments(
        self,
        entity: Any,
    ) -> list[Segment]:
        """Извлекает line-like сегменты entity."""
        entity_type = entity.dxftype()

        try:
            if entity_type == "LINE":
                return [
                    (
                        self._xy(
                            entity.dxf.start,
                        ),
                        self._xy(
                            entity.dxf.end,
                        ),
                    )
                ]

            if entity_type == "LWPOLYLINE":
                points = [
                    (
                        round(
                            float(x),
                            6,
                        ),
                        round(
                            float(y),
                            6,
                        ),
                    )
                    for (
                        x,
                        y,
                        *_rest,
                    ) in entity.get_points()
                ]

                segments = list(
                    pairwise(
                        points,
                    )
                )

                if entity.closed and len(points) > 2:
                    segments.append(
                        (
                            points[-1],
                            points[0],
                        )
                    )

                return segments

            if entity_type == "POLYLINE":
                points = [
                    self._xy(
                        vertex.dxf.location,
                    )
                    for vertex in entity.vertices
                ]

                segments = list(
                    pairwise(
                        points,
                    )
                )

                if entity.is_closed and len(points) > 2:
                    segments.append(
                        (
                            points[-1],
                            points[0],
                        )
                    )

                return segments

            if entity_type == "ARC":
                return [
                    (
                        self._xy(
                            entity.start_point,
                        ),
                        self._xy(
                            entity.end_point,
                        ),
                    )
                ]
        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return []

        return []

    def _iter_expanded_entities(
        self,
        entities: Iterable[Any],
        *,
        depth: int = 0,
    ) -> Iterable[Any]:
        """Разворачивает INSERT в virtual entities."""
        for entity in entities:
            yield entity

            if entity.dxftype() != "INSERT" or depth >= self._virtual_insert_depth:
                continue

            try:
                virtual_entities = list(entity.virtual_entities())
            except Exception:
                continue

            yield from self._iter_expanded_entities(
                virtual_entities,
                depth=depth + 1,
            )

    def _node_key(
        self,
        point: Point,
    ) -> tuple[int, int]:
        """Квантизует endpoint с заданной tolerance."""
        tolerance = max(
            self._connectivity_tolerance,
            1e-9,
        )

        return (
            round(point[0] / tolerance),
            round(point[1] / tolerance),
        )

    def _collect_machine_data(
        self,
        *,
        document: ezdxf.document.Drawing,
        selected_layout: Any,
        layout_stats: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Собирает машинное представление CAD-листа."""
        expanded_entities = list(
            self._iter_expanded_entities(
                selected_layout,
            )
        )

        entity_counts: Counter[str] = Counter()
        layer_counts: Counter[str] = Counter()

        texts: list[dict[str, Any]] = []
        blocks: list[dict[str, Any]] = []
        segments: list[Segment] = []

        min_x = math.inf
        min_y = math.inf
        max_x = -math.inf
        max_y = -math.inf

        def update_extent(
            point: Point,
        ) -> None:
            nonlocal min_x
            nonlocal min_y
            nonlocal max_x
            nonlocal max_y

            min_x = min(
                min_x,
                point[0],
            )

            min_y = min(
                min_y,
                point[1],
            )

            max_x = max(
                max_x,
                point[0],
            )

            max_y = max(
                max_y,
                point[1],
            )

        for entity in expanded_entities:
            entity_type = entity.dxftype()

            entity_counts[entity_type] += 1

            layer = str(
                getattr(
                    entity.dxf,
                    "layer",
                    "",
                )
                or ""
            )

            if layer:
                layer_counts[layer] += 1

            self._collect_text(
                entity,
                entity_type=entity_type,
                layer=layer,
                texts=texts,
            )

            self._collect_block(
                entity,
                entity_type=entity_type,
                layer=layer,
                texts=texts,
                blocks=blocks,
            )

            for segment in self._entity_segments(
                entity,
            ):
                segments.append(
                    segment,
                )

                update_extent(
                    segment[0],
                )

                update_extent(
                    segment[1],
                )

        node_degree: defaultdict[
            tuple[int, int],
            int,
        ] = defaultdict(
            int,
        )

        node_points: dict[
            tuple[int, int],
            Point,
        ] = {}

        for start, end in segments:
            start_key = self._node_key(
                start,
            )

            end_key = self._node_key(
                end,
            )

            node_degree[start_key] += 1

            node_degree[end_key] += 1

            node_points.setdefault(
                start_key,
                start,
            )

            node_points.setdefault(
                end_key,
                end,
            )

        dangling_points = [
            node_points[key]
            for (
                key,
                degree,
            ) in node_degree.items()
            if degree == 1
        ]

        junction_count = sum(1 for degree in node_degree.values() if degree >= 3)

        units_code = int(
            document.header.get(
                "$INSUNITS",
                0,
            )
            or 0
        )

        extent = self._build_extent(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        )

        return {
            "dxf_version": str(
                document.dxfversion,
            ),
            "insert_units_code": units_code,
            "insert_units": _INSERT_UNITS.get(
                units_code,
                "unknown",
            ),
            "selected_layout": str(
                selected_layout.name,
            ),
            "layouts": layout_stats,
            "expanded_entity_count": len(
                expanded_entities,
            ),
            "entity_counts": dict(entity_counts.most_common()),
            "layer_counts": dict(
                layer_counts.most_common(
                    80,
                )
            ),
            "texts": texts,
            "block_inserts": blocks,
            "geometry": {
                "segment_count": len(
                    segments,
                ),
                "endpoint_node_count": len(
                    node_degree,
                ),
                "dangling_endpoint_count": len(
                    dangling_points,
                ),
                "junction_count": (junction_count),
                "connectivity_tolerance_drawing_units": (self._connectivity_tolerance),
                "dangling_endpoint_sample": (
                    dangling_points[: self._dangling_sample_limit]
                ),
                "extent": extent,
            },
        }

    def _collect_text(
        self,
        entity: Any,
        *,
        entity_type: str,
        layer: str,
        texts: list[dict[str, Any]],
    ) -> None:
        """Добавляет TEXT или MTEXT в machine context."""
        if len(texts) >= self._text_sample_limit:
            return

        if entity_type == "TEXT":
            text = str(
                getattr(
                    entity.dxf,
                    "text",
                    "",
                )
                or ""
            ).strip()

            if not text:
                return

            texts.append(
                {
                    "type": "TEXT",
                    "layer": layer,
                    "text": text[:500],
                    "insert": self._xy(
                        getattr(
                            entity.dxf,
                            "insert",
                            (0.0, 0.0),
                        )
                    ),
                }
            )

            return

        if entity_type != "MTEXT":
            return

        try:
            text = str(
                entity.plain_text(),
            ).strip()
        except Exception:
            text = str(
                getattr(
                    entity,
                    "text",
                    "",
                )
            ).strip()

        if not text:
            return

        texts.append(
            {
                "type": "MTEXT",
                "layer": layer,
                "text": text[:900],
                "insert": self._xy(
                    getattr(
                        entity.dxf,
                        "insert",
                        (0.0, 0.0),
                    )
                ),
            }
        )

    def _collect_block(
        self,
        entity: Any,
        *,
        entity_type: str,
        layer: str,
        texts: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
    ) -> None:
        """Добавляет INSERT и ATTRIB в machine context."""
        if entity_type != "INSERT" or len(blocks) >= self._block_sample_limit:
            return

        insert_point = self._xy(
            getattr(
                entity.dxf,
                "insert",
                (0.0, 0.0),
            )
        )

        blocks.append(
            {
                "name": str(
                    getattr(
                        entity.dxf,
                        "name",
                        "",
                    )
                ),
                "layer": layer,
                "insert": insert_point,
                "rotation": float(
                    getattr(
                        entity.dxf,
                        "rotation",
                        0.0,
                    )
                    or 0.0
                ),
                "xscale": float(
                    getattr(
                        entity.dxf,
                        "xscale",
                        1.0,
                    )
                    or 1.0
                ),
                "yscale": float(
                    getattr(
                        entity.dxf,
                        "yscale",
                        1.0,
                    )
                    or 1.0
                ),
            }
        )

        try:
            attributes = entity.attribs
        except Exception:
            return

        for attribute in attributes:
            if len(texts) >= self._text_sample_limit:
                break

            value = str(
                getattr(
                    attribute.dxf,
                    "text",
                    "",
                )
                or ""
            ).strip()

            if not value:
                continue

            texts.append(
                {
                    "type": "ATTRIB",
                    "layer": str(
                        getattr(
                            attribute.dxf,
                            "layer",
                            layer,
                        )
                    ),
                    "tag": str(
                        getattr(
                            attribute.dxf,
                            "tag",
                            "",
                        )
                    ),
                    "text": value[:500],
                    "insert": self._xy(
                        getattr(
                            attribute.dxf,
                            "insert",
                            insert_point,
                        )
                    ),
                }
            )

    @staticmethod
    def _build_extent(
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> dict[str, Any] | None:
        """Строит bounding extent найденной геометрии."""
        if math.isinf(
            min_x,
        ):
            return None

        return {
            "min": (
                round(
                    min_x,
                    6,
                ),
                round(
                    min_y,
                    6,
                ),
            ),
            "max": (
                round(
                    max_x,
                    6,
                ),
                round(
                    max_y,
                    6,
                ),
            ),
            "width": round(
                max_x - min_x,
                6,
            ),
            "height": round(
                max_y - min_y,
                6,
            ),
        }
