# services/pdf-service/app/cad.py

"""Подготовка DXF/DWG для совместного анализа с PDF и нормативной базой."""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import ezdxf
from ezdxf import recover
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as drawing_layout
from ezdxf.addons.drawing import pymupdf as drawing_pymupdf
from PIL import Image, ImageDraw


CAD_ALLOWED_EXTENSIONS = {".dxf", ".dwg"}

CAD_DWG_CONVERTER_COMMAND = os.getenv(
    "CAD_DWG_CONVERTER_COMMAND",
    "dwg2dxf",
)

CAD_DWG_CONVERTER_TIMEOUT = int(
    os.getenv(
        "CAD_DWG_CONVERTER_TIMEOUT",
        "180",
    )
)

CAD_RENDER_DPI = int(
    os.getenv(
        "CAD_RENDER_DPI",
        "180",
    )
)

CAD_RENDER_MAX_SIDE = int(
    os.getenv(
        "CAD_RENDER_MAX_SIDE",
        "2600",
    )
)

CAD_COMBINED_MAX_SIDE = int(
    os.getenv(
        "CAD_COMBINED_MAX_SIDE",
        "3200",
    )
)

CAD_MACHINE_TEXT_LIMIT = int(
    os.getenv(
        "CAD_MACHINE_TEXT_LIMIT",
        "14000",
    )
)

CAD_TEXT_SAMPLE_LIMIT = int(
    os.getenv(
        "CAD_TEXT_SAMPLE_LIMIT",
        "160",
    )
)

CAD_BLOCK_SAMPLE_LIMIT = int(
    os.getenv(
        "CAD_BLOCK_SAMPLE_LIMIT",
        "120",
    )
)

CAD_DANGLING_SAMPLE_LIMIT = int(
    os.getenv(
        "CAD_DANGLING_SAMPLE_LIMIT",
        "120",
    )
)

CAD_CONNECTIVITY_TOLERANCE = float(
    os.getenv(
        "CAD_CONNECTIVITY_TOLERANCE",
        "0.5",
    )
)

CAD_VIRTUAL_INSERT_DEPTH = int(
    os.getenv(
        "CAD_VIRTUAL_INSERT_DEPTH",
        "2",
    )
)


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


def get_cad_capabilities() -> dict[str, Any]:
    """Вернуть возможности текущего контейнера CAD."""

    converter_path = shutil.which(
        CAD_DWG_CONVERTER_COMMAND
    )

    return {
        "dxf": True,
        "dwg": converter_path is not None,
        "dwg_converter": converter_path,
        "dwg_converter_command": (
            CAD_DWG_CONVERTER_COMMAND
        ),
    }


def validate_cad_filename(
    filename: str | None,
) -> str:
    """Проверить расширение CAD-файла и вернуть его в lowercase."""

    if not filename:
        raise ValueError(
            "У CAD-файла отсутствует имя."
        )

    extension = Path(
        filename
    ).suffix.lower()

    if extension not in CAD_ALLOWED_EXTENSIONS:
        raise ValueError(
            "Поддерживаются только DWG и DXF. "
            f"Получено расширение: {extension or '<нет>'}."
        )

    return extension


def _normalize_to_dxf(
    *,
    cad_bytes: bytes,
    filename: str,
    workdir: Path,
) -> tuple[
    Path,
    bool,
    list[str],
]:
    """Сохранить DXF или преобразовать DWG в DXF через LibreDWG."""

    extension = validate_cad_filename(
        filename
    )

    warnings: list[str] = []

    safe_stem = (
        Path(filename).stem
        or "drawing"
    )

    if extension == ".dxf":
        dxf_path = (
            workdir
            / f"{safe_stem}.dxf"
        )

        dxf_path.write_bytes(
            cad_bytes
        )

        return (
            dxf_path,
            False,
            warnings,
        )

    converter = shutil.which(
        CAD_DWG_CONVERTER_COMMAND
    )

    if converter is None:
        raise RuntimeError(
            "DWG-конвертер недоступен в контейнере. "
            "Ожидалась команда dwg2dxf."
        )

    dwg_path = (
        workdir
        / f"{safe_stem}.dwg"
    )

    dwg_path.write_bytes(
        cad_bytes
    )

    command = [
        converter,
        "--overwrite",
        dwg_path.name,
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=(
                CAD_DWG_CONVERTER_TIMEOUT
            ),
            check=False,
        )

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Конвертация DWG → DXF превысила "
            f"{CAD_DWG_CONVERTER_TIMEOUT} секунд."
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            "Не удалось преобразовать DWG в DXF. "
            f"exit_code={completed.returncode}; "
            f"stdout={completed.stdout[-1000:]}; "
            f"stderr={completed.stderr[-1000:]}"
        )

    candidates = sorted(
        workdir.glob(
            "*.dxf"
        )
    )

    if not candidates:
        raise RuntimeError(
            "DWG-конвертер завершился без ошибки, "
            "но DXF-файл не был создан."
        )

    exact_candidate = (
        workdir
        / f"{safe_stem}.dxf"
    )

    dxf_path = (
        exact_candidate
        if exact_candidate.is_file()
        else candidates[0]
    )

    warnings.append(
        "Исходный DWG автоматически преобразован "
        "в DXF перед анализом."
    )

    return (
        dxf_path,
        True,
        warnings,
    )


def _load_dxf(
    dxf_path: Path,
) -> tuple[
    ezdxf.document.Drawing,
    list[str],
]:
    """Открыть DXF с попыткой recovery повреждённых структур."""

    warnings: list[str] = []

    try:
        document, auditor = (
            recover.readfile(
                dxf_path
            )
        )

    except (
        OSError,
        ezdxf.DXFError,
    ) as exc:
        raise ValueError(
            "Не удалось прочитать DXF. "
            f"Причина: {exc}"
        ) from exc

    if auditor.has_errors:
        warnings.append(
            "DXF содержит структурные ошибки; "
            "ezdxf выполнил recovery."
        )

    if auditor.has_fixes:
        warnings.append(
            "При чтении DXF автоматически применены исправления."
        )

    return (
        document,
        warnings,
    )


def _layout_entity_count(
    layout: Any,
) -> int:
    """Безопасно посчитать сущности layout."""

    try:
        return len(
            layout
        )
    except TypeError:
        return sum(
            1
            for _ in layout
        )


def _select_layout(
    document: ezdxf.document.Drawing,
) -> tuple[
    Any,
    list[str],
    list[dict[str, Any]],
]:
    """Выбрать один CAD-layout, соответствующий MVP одного листа."""

    warnings: list[str] = []
    layout_stats: list[
        dict[str, Any]
    ] = []

    paper_layouts = []

    for name in (
        document.layout_names()
    ):
        layout = (
            document.layouts.get(
                name
            )
        )

        entity_count = (
            _layout_entity_count(
                layout
            )
        )

        is_model = (
            name.lower()
            == "model"
        )

        layout_stats.append(
            {
                "name": name,
                "is_modelspace": (
                    is_model
                ),
                "entity_count": (
                    entity_count
                ),
            }
        )

        if (
            not is_model
            and entity_count > 0
        ):
            paper_layouts.append(
                layout
            )

    if len(
        paper_layouts
    ) == 1:
        return (
            paper_layouts[0],
            warnings,
            layout_stats,
        )

    if len(
        paper_layouts
    ) > 1:
        warnings.append(
            "В CAD-файле обнаружено несколько непустых "
            "paper-space layouts. Для MVP выбран первый; "
            "рекомендуется передавать файл одного листа."
        )

        return (
            paper_layouts[0],
            warnings,
            layout_stats,
        )

    return (
        document.modelspace(),
        warnings,
        layout_stats,
    )


def _xy(
    value: Any,
) -> tuple[float, float]:
    """Привести Vec2/Vec3/tuple к двум координатам."""

    return (
        round(
            float(
                value[0]
            ),
            6,
        ),
        round(
            float(
                value[1]
            ),
            6,
        ),
    )


def _entity_segments(
    entity: Any,
) -> list[
    tuple[
        tuple[float, float],
        tuple[float, float],
    ]
]:
    """Извлечь line-like сегменты для простой проверки связности."""

    entity_type = (
        entity.dxftype()
    )

    try:
        if entity_type == "LINE":
            return [
                (
                    _xy(
                        entity.dxf.start
                    ),
                    _xy(
                        entity.dxf.end
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
                zip(
                    points,
                    points[1:],
                    strict=False,
                )
            )

            if (
                entity.closed
                and len(points) > 2
            ):
                segments.append(
                    (
                        points[-1],
                        points[0],
                    )
                )

            return segments

        if entity_type == "POLYLINE":
            points = [
                _xy(
                    vertex.dxf.location
                )
                for vertex in (
                    entity.vertices
                )
            ]

            segments = list(
                zip(
                    points,
                    points[1:],
                    strict=False,
                )
            )

            if (
                entity.is_closed
                and len(points) > 2
            ):
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
                    _xy(
                        entity.start_point
                    ),
                    _xy(
                        entity.end_point
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
    entities: Iterable[Any],
    *,
    depth: int = 0,
) -> Iterable[Any]:
    """Развернуть INSERT в virtual entities для машинного анализа геометрии."""

    for entity in entities:
        yield entity

        if (
            entity.dxftype()
            != "INSERT"
            or depth
            >= CAD_VIRTUAL_INSERT_DEPTH
        ):
            continue

        try:
            virtual_entities = list(
                entity.virtual_entities()
            )
        except Exception:
            continue

        yield from _iter_expanded_entities(
            virtual_entities,
            depth=(
                depth + 1
            ),
        )


def _node_key(
    point: tuple[
        float,
        float,
    ],
) -> tuple[int, int]:
    """Квантизовать координаты для приблизительного endpoint graph."""

    tolerance = max(
        CAD_CONNECTIVITY_TOLERANCE,
        1e-9,
    )

    return (
        int(
            round(
                point[0]
                / tolerance
            )
        ),
        int(
            round(
                point[1]
                / tolerance
            )
        ),
    )


def _collect_machine_data(
    *,
    document: ezdxf.document.Drawing,
    selected_layout: Any,
    layout_stats: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    """Собрать компактное машинное представление CAD-листа."""

    expanded_entities = list(
        _iter_expanded_entities(
            selected_layout
        )
    )

    entity_counts: Counter[str] = (
        Counter()
    )

    layer_counts: Counter[str] = (
        Counter()
    )

    texts: list[
        dict[str, Any]
    ] = []

    blocks: list[
        dict[str, Any]
    ] = []

    segments: list[
        tuple[
            tuple[float, float],
            tuple[float, float],
        ]
    ] = []

    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    def update_extent(
        point: tuple[
            float,
            float,
        ],
    ) -> None:
        nonlocal min_x, min_y, max_x, max_y

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

    # Python не разрешает nonlocal (...) — оставим обычное присваивание
    for entity in expanded_entities:
        entity_type = (
            entity.dxftype()
        )

        entity_counts[
            entity_type
        ] += 1

        layer = str(
            getattr(
                entity.dxf,
                "layer",
                "",
            )
            or ""
        )

        if layer:
            layer_counts[
                layer
            ] += 1

        if (
            entity_type == "TEXT"
            and len(texts)
            < CAD_TEXT_SAMPLE_LIMIT
        ):
            text = str(
                getattr(
                    entity.dxf,
                    "text",
                    "",
                )
                or ""
            ).strip()

            if text:
                insert = _xy(
                    getattr(
                        entity.dxf,
                        "insert",
                        (0.0, 0.0),
                    )
                )

                texts.append(
                    {
                        "type": "TEXT",
                        "layer": layer,
                        "text": text[:500],
                        "insert": insert,
                    }
                )

        elif (
            entity_type == "MTEXT"
            and len(texts)
            < CAD_TEXT_SAMPLE_LIMIT
        ):
            try:
                text = str(
                    entity.plain_text()
                ).strip()
            except Exception:
                text = str(
                    getattr(
                        entity,
                        "text",
                        "",
                    )
                ).strip()

            if text:
                insert = _xy(
                    getattr(
                        entity.dxf,
                        "insert",
                        (0.0, 0.0),
                    )
                )

                texts.append(
                    {
                        "type": "MTEXT",
                        "layer": layer,
                        "text": text[:900],
                        "insert": insert,
                    }
                )

        if (
            entity_type == "INSERT"
            and len(blocks)
            < CAD_BLOCK_SAMPLE_LIMIT
        ):
            insert_point = _xy(
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
                    "insert": (
                        insert_point
                    ),
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
                for attrib in (
                    entity.attribs
                ):
                    if (
                        len(texts)
                        >= CAD_TEXT_SAMPLE_LIMIT
                    ):
                        break

                    value = str(
                        getattr(
                            attrib.dxf,
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
                                    attrib.dxf,
                                    "layer",
                                    layer,
                                )
                            ),
                            "tag": str(
                                getattr(
                                    attrib.dxf,
                                    "tag",
                                    "",
                                )
                            ),
                            "text": value[:500],
                            "insert": _xy(
                                getattr(
                                    attrib.dxf,
                                    "insert",
                                    insert_point,
                                )
                            ),
                        }
                    )
            except Exception:
                pass

        entity_segments = (
            _entity_segments(
                entity
            )
        )

        for segment in (
            entity_segments
        ):
            segments.append(
                segment
            )

            update_extent(
                segment[0]
            )
            update_extent(
                segment[1]
            )

    node_degree: defaultdict[
        tuple[int, int],
        int,
    ] = defaultdict(
        int
    )

    node_points: dict[
        tuple[int, int],
        tuple[float, float],
    ] = {}

    for (
        start,
        end,
    ) in segments:
        start_key = _node_key(
            start
        )
        end_key = _node_key(
            end
        )

        node_degree[
            start_key
        ] += 1
        node_degree[
            end_key
        ] += 1

        node_points.setdefault(
            start_key,
            start,
        )
        node_points.setdefault(
            end_key,
            end,
        )

    dangling_points = [
        node_points[
            key
        ]
        for (
            key,
            degree,
        ) in node_degree.items()
        if degree == 1
    ]

    junction_count = sum(
        1
        for degree in (
            node_degree.values()
        )
        if degree >= 3
    )

    units_code = int(
        document.header.get(
            "$INSUNITS",
            0,
        )
        or 0
    )

    if math.isinf(
        min_x
    ):
        extent = None
    else:
        extent = {
            "min": (
                round(min_x, 6),
                round(min_y, 6),
            ),
            "max": (
                round(max_x, 6),
                round(max_y, 6),
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

    return {
        "dxf_version": str(
            document.dxfversion
        ),
        "insert_units_code": (
            units_code
        ),
        "insert_units": (
            _INSERT_UNITS.get(
                units_code,
                "unknown",
            )
        ),
        "selected_layout": str(
            selected_layout.name
        ),
        "layouts": layout_stats,
        "expanded_entity_count": len(
            expanded_entities
        ),
        "entity_counts": dict(
            entity_counts.most_common()
        ),
        "layer_counts": dict(
            layer_counts.most_common(
                80
            )
        ),
        "texts": texts,
        "block_inserts": blocks,
        "geometry": {
            "segment_count": len(
                segments
            ),
            "endpoint_node_count": len(
                node_degree
            ),
            "dangling_endpoint_count": len(
                dangling_points
            ),
            "junction_count": (
                junction_count
            ),
            "connectivity_tolerance_drawing_units": (
                CAD_CONNECTIVITY_TOLERANCE
            ),
            "dangling_endpoint_sample": (
                dangling_points[
                    :CAD_DANGLING_SAMPLE_LIMIT
                ]
            ),
            "extent": extent,
        },
    }


def _render_layout(
    *,
    document: ezdxf.document.Drawing,
    selected_layout: Any,
) -> bytes:
    """Отрендерить выбранный DXF layout в PNG через ezdxf/PyMuPDF backend."""

    try:
        backend = (
            drawing_pymupdf.PyMuPdfBackend()
        )

        Frontend(
            RenderContext(
                document
            ),
            backend,
        ).draw_layout(
            selected_layout
        )

        image_bytes = (
            backend.get_pixmap_bytes(
                drawing_layout.Page(
                    0,
                    0,
                ),
                fmt="png",
                dpi=CAD_RENDER_DPI,
            )
        )

    except Exception as exc:
        raise RuntimeError(
            "Не удалось отрендерить DXF в изображение: "
            f"{exc}"
        ) from exc

    image = Image.open(
        io.BytesIO(
            image_bytes
        )
    ).convert(
        "RGB"
    )

    image.thumbnail(
        (
            CAD_RENDER_MAX_SIDE,
            CAD_RENDER_MAX_SIDE,
        ),
        Image.Resampling.LANCZOS,
    )

    output = io.BytesIO()

    image.save(
        output,
        format="PNG",
        optimize=True,
    )

    return output.getvalue()


def _build_machine_context(
    machine_data: dict[str, Any],
) -> str:
    """Сформировать ограниченный машинный контекст для VLM/RAG."""

    serialized = json.dumps(
        machine_data,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    return serialized[
        :CAD_MACHINE_TEXT_LIMIT
    ]


def analyze_cad_bytes(
    *,
    cad_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    """Нормализовать CAD, распарсить и отрендерить один лист."""

    if not cad_bytes:
        raise ValueError(
            "Передан пустой CAD-файл."
        )

    original_extension = (
        validate_cad_filename(
            filename
        )
    )

    with tempfile.TemporaryDirectory(
        prefix="pdrd-cad-"
    ) as tmp:
        workdir = Path(
            tmp
        )

        (
            dxf_path,
            converted_from_dwg,
            conversion_warnings,
        ) = _normalize_to_dxf(
            cad_bytes=(
                cad_bytes
            ),
            filename=(
                filename
            ),
            workdir=workdir,
        )

        (
            document,
            load_warnings,
        ) = _load_dxf(
            dxf_path
        )

        (
            selected_layout,
            layout_warnings,
            layout_stats,
        ) = _select_layout(
            document
        )

        machine_data = (
            _collect_machine_data(
                document=document,
                selected_layout=(
                    selected_layout
                ),
                layout_stats=(
                    layout_stats
                ),
            )
        )

        render_bytes = (
            _render_layout(
                document=document,
                selected_layout=(
                    selected_layout
                ),
            )
        )

    warnings = [
        *conversion_warnings,
        *load_warnings,
        *layout_warnings,
    ]

    return {
        "original_file_name": (
            filename
        ),
        "original_format": (
            original_extension.lstrip(
                "."
            )
        ),
        "normalized_format": "dxf",
        "converted_from_dwg": (
            converted_from_dwg
        ),
        "selected_layout": (
            machine_data[
                "selected_layout"
            ]
        ),
        "warnings": warnings,
        "machine_data": (
            machine_data
        ),
        "machine_context": (
            _build_machine_context(
                machine_data
            )
        ),
        "render_bytes": (
            render_bytes
        ),
    }


def _fit_image(
    image: Image.Image,
    max_side: int,
) -> Image.Image:
    """Уменьшить raster без увеличения исходника."""

    result = image.copy()

    result.thumbnail(
        (
            max_side,
            max_side,
        ),
        Image.Resampling.LANCZOS,
    )

    return result


def combine_source_images(
    *,
    pdf_image_bytes: bytes,
    cad_image_bytes: bytes,
) -> bytes:
    """Собрать PDF и CAD render в одно изображение для одного VLM-вызова."""

    pdf_image = Image.open(
        io.BytesIO(
            pdf_image_bytes
        )
    ).convert(
        "RGB"
    )

    cad_image = Image.open(
        io.BytesIO(
            cad_image_bytes
        )
    ).convert(
        "RGB"
    )

    panel_max = max(
        800,
        CAD_COMBINED_MAX_SIDE
        // 2,
    )

    pdf_image = _fit_image(
        pdf_image,
        panel_max,
    )

    cad_image = _fit_image(
        cad_image,
        panel_max,
    )

    header_height = 34
    gap = 18

    width = (
        pdf_image.width
        + cad_image.width
        + gap
    )

    height = max(
        pdf_image.height,
        cad_image.height,
    ) + header_height

    canvas = Image.new(
        "RGB",
        (
            width,
            height,
        ),
        "white",
    )

    draw = ImageDraw.Draw(
        canvas
    )

    draw.text(
        (
            8,
            8,
        ),
        "PDF",
        fill="black",
    )

    cad_x = (
        pdf_image.width
        + gap
    )

    draw.text(
        (
            cad_x + 8,
            8,
        ),
        "CAD (DXF render)",
        fill="black",
    )

    canvas.paste(
        pdf_image,
        (
            0,
            header_height,
        ),
    )

    canvas.paste(
        cad_image,
        (
            cad_x,
            header_height,
        ),
    )

    canvas.thumbnail(
        (
            CAD_COMBINED_MAX_SIDE,
            CAD_COMBINED_MAX_SIDE,
        ),
        Image.Resampling.LANCZOS,
    )

    output = io.BytesIO()

    canvas.save(
        output,
        format="PNG",
        optimize=True,
    )

    return output.getvalue()


def build_cad_augmented_text(
    *,
    pdf_text: str | None,
    cad_result: dict[str, Any],
) -> str:
    """Объединить текст PDF и машинное представление CAD."""

    sections = [
        "=== CAD MACHINE REPRESENTATION ===",
        (
            "CAD-файл соответствует одному анализируемому листу. "
            "Машинные данные получены детерминированным парсером ezdxf; "
            "сырые DXF group codes в LLM не передаются."
        ),
        cad_result[
            "machine_context"
        ],
    ]

    if pdf_text is not None:
        sections = [
            "=== PDF TEXT ===",
            pdf_text,
            "",
            "=== RELATION BETWEEN SOURCES ===",
            (
                "PDF и CAD заявлены пользователем как два представления "
                "одного и того же листа. Используй PDF render для визуального "
                "контекста, а CAD machine representation — для геометрии, "
                "слоёв, блоков, подписей и связности."
            ),
            "",
            *sections,
        ]

    return "\n".join(
        sections
    )


def compact_cad_for_api(
    cad_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Вернуть во frontend только полезную диагностику CAD."""

    if cad_result is None:
        return None

    machine_data = cad_result[
        "machine_data"
    ]

    return {
        "original_file_name": (
            cad_result[
                "original_file_name"
            ]
        ),
        "original_format": (
            cad_result[
                "original_format"
            ]
        ),
        "normalized_format": (
            cad_result[
                "normalized_format"
            ]
        ),
        "converted_from_dwg": (
            cad_result[
                "converted_from_dwg"
            ]
        ),
        "selected_layout": (
            cad_result[
                "selected_layout"
            ]
        ),
        "warnings": (
            cad_result[
                "warnings"
            ]
        ),
        "dxf_version": (
            machine_data[
                "dxf_version"
            ]
        ),
        "insert_units": (
            machine_data[
                "insert_units"
            ]
        ),
        "expanded_entity_count": (
            machine_data[
                "expanded_entity_count"
            ]
        ),
        "entity_counts": (
            machine_data[
                "entity_counts"
            ]
        ),
        "geometry": (
            machine_data[
                "geometry"
            ]
        ),
    }
