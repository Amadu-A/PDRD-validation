# services/analysis-service/src/pdrd_analysis_service/domain/physical_identity.py

"""Предметная привязка существующих фактов к локальным элементам PDF.

Векторная цепочка подтверждает только локальный отрезок страницы. Её нельзя
переносить на другой лист по одинаковой метке сети. Неоднозначность сохраняется.
"""

import hashlib
import math
import re
from dataclasses import replace

from pdrd_analysis_service.domain.document_context import (
    document_fact_from_mapping,
    normalize_label,
)


def pdf_label_facts(text_words, *, page, page_text):
    """Читает однострочные подписанные параметры из уже извлечённых PDF-блоков.

    Отдельное L=… из соседнего блока не присоединяется по расстоянию. Эти записи
    дополняют VLM-факты без их замены и без присвоения общего участка сети.
    """
    groups = {}
    text_key = normalize_label(page_text)
    for word in text_words:
        if not all(
            isinstance(word.get(key), int) and not isinstance(word[key], bool)
            for key in ("block_no", "line_no", "word_no")
        ):
            continue
        groups.setdefault((word.get("block_no"), word.get("line_no")), []).append(word)
    result = []
    number = r"\d+(?:[.,]\d+)?"
    for (block, line), words in groups.items():
        quote = " ".join(
            str(word.get("text", ""))
            for word in sorted(words, key=lambda word: word.get("word_no", 0))
        ).strip()
        if normalize_label(quote) not in text_key:
            continue
        tags = set(re.findall(r"(?<![\w/])[А-ЯA-Z]{1,3}\d+(?:-\d+)?(?![\w/])", quote))
        if len(tags) != 1:
            continue
        tag = next(iter(tags))
        boxes = [word["bbox"] for word in words if valid_box(word.get("bbox"))]
        if len(boxes) != len(words):
            continue
        region = {
            name: fn(box[name] for box in boxes)
            for name, fn in (
                ("x_min", min),
                ("y_min", min),
                ("x_max", max),
                ("y_max", max),
            )
        }
        region["confidence"] = 1.0
        for pattern, prop, unit, role in (
            (
                r"[Øø⌀]" + number + r"(?:[xх×*]" + number + r")?",
                "Диаметр",
                "",
                "pipe_size",
            ),
            (
                r"(?<![A-Za-zА-Яа-я])L\s*=\s*(?P<value>" + number + r")\s*м\b",
                "Длина",
                "м",
                "segment_length",
            ),
        ):
            for match in re.finditer(pattern, quote, re.I):
                value = match.groupdict().get("value") or match[0]
                fact = document_fact_from_mapping(
                    {
                        "kind": "parameter",
                        "subject_type": "system",
                        "identifier": tag,
                        "property_type": "parameter",
                        "property_name": prop,
                        "value_raw": value,
                        "unit_raw": unit,
                        "value_role": role,
                        "evidence_text": quote,
                        "visual_regions": [region],
                        "source_regions": [region],
                        "source_pages": [page],
                        "source_item_id": f"pdf-label-p{page}-b{block}-l{line}",
                        "extraction_method": "pdf_text",
                        "column_binding": "pdf_label",
                    }
                )
                if fact is not None:
                    result.append(fact)
    return tuple(result)


def geometric_property(fact):
    """Отделяет размеры и отметки от системных расходов и назначения."""
    if fact.subject_type in {
        "document_reference",
        "document_topic",
        "document",
        "section",
    } or fact.relation in {"references", "defines_reference", "topic_reference"}:
        return False
    return bool(
        re.search(
            r"диаметр|толщин|типоразмер|длин|протяж|отметк|высот|diameter|length|elevation",
            fact.property_name + " " + fact.property_type,
            re.I,
        )
    )


def object_kind(fact):
    """Не приравнивает сухотруб или футляр к основному трубопроводу."""
    text = normalize_label(
        fact.subject_type + " " + fact.subject_name + " " + fact.evidence_text
    )
    for pattern, kind in (
        (r"сухотруб|dry_pipe", "dry_pipe"),
        (r"футляр|casing", "casing"),
    ):
        if re.search(pattern, text):
            return kind
    return normalize_label(fact.object_kind)


def value_role(fact):
    """Роль величины подтверждается подписью, а не совпадением тега."""
    text = normalize_label(fact.evidence_text + " " + fact.row_label)
    if re.search(
        r"(?:общая|полная|суммарная)\s+(?:длина|протяж)|протяженность\s+(?:проектируем\w*\s+)?сет",
        text,
    ):
        return "network_total_length"
    if re.search(r"отметк|elevation", fact.property_name + fact.property_type, re.I):
        return "point_elevation"
    role = normalize_label(fact.value_role)
    return "" if role == "network_total_length" else role


def physical_key(item):
    """Возвращает только доказанную границу участка; пустота означает неизвестность."""
    fact = item.fact
    quote = normalize_label(fact.evidence_text)
    from pdrd_analysis_service.domain.evidence_audit import literal_contains

    kind = object_kind(fact)
    if value_role(fact) == "network_total_length":
        return ("network_total", kind)
    point_value = value_role(fact) == "point_elevation"
    first, second = (
        normalize_label(fact.segment_start),
        normalize_label(fact.segment_end),
    )
    endpoints = bool(
        first
        and second
        and re.search(
            r"(?<!\w)(?:от\s+"
            + re.escape(first)
            + r"\s+до\s+"
            + re.escape(second)
            + r"|"
            + re.escape(first)
            + r"\s*[-—–]\s*"
            + re.escape(second)
            + r")(?![\w.])",
            quote,
        )
    )
    if (
        not point_value
        and endpoints
        and all(
            normalize_label(node) in quote
            for node in (fact.segment_start, fact.segment_end)
        )
    ):
        return (
            "nodes",
            kind,
            *sorted(
                (normalize_label(fact.segment_start), normalize_label(fact.segment_end))
            ),
        )
    segment = normalize_label(fact.scope_segment)
    tag_only = any(
        re.fullmatch(
            r"(?:(?:участок|ввод|сеть|система|трубопровод)\s+)?"
            + re.escape(normalize_label(tag)),
            segment,
        )
        for tag in (fact.identifier, fact.scope_system)
        if tag
    )
    named_segment = bool(re.search(r"\d|\b[A-ZА-Я]\b", fact.scope_segment)) and bool(
        re.fullmatch(
            r"(?:участок|отрезок|узел|точка|segment|node|point)\s+[-\w.—–]+", segment
        )
        or re.search(
            r"(?:участок|отрезок|узел|точка|segment|node|point)\s+"
            + re.escape(segment)
            + r"(?!\w)",
            quote,
        )
    )
    if point_value and re.search(r"участок|сеть|трубопровод", segment):
        named_segment = False
    if (
        segment
        and named_segment
        and not tag_only
        and segment
        not in {
            normalize_label(fact.identifier),
            normalize_label(fact.scope_system),
        }
        and literal_contains(quote, segment)
    ):
        return ("explicit_segment", kind, segment)
    binding = fact.geometry_binding or {}
    # Только программная трассировка, не самооценка VLM и не произвольный hash из ответа.
    if (
        not point_value
        and binding.get("method") == "pdf_vector_chain"
        and binding.get("status") == "bound"
    ):
        lines = binding.get("target_lines") or []
        if lines and binding.get("label_matches") == 1:
            return ("page_element", kind, item.page, binding.get("element_id"))
    return None


def comparison_identity(items):
    """Разделяет разные объекты, неподтверждённую связь и общий проверяемый отрезок."""
    if not items or not any(geometric_property(item.fact) for item in items):
        return "not_required"
    kinds = {object_kind(item.fact) for item in items} - {""}
    if len(kinds) > 1 or (
        kinds & {"dry_pipe", "casing"}
        and any(not object_kind(item.fact) for item in items)
    ):
        return "different_object_kinds"
    roles = {value_role(item.fact) for item in items} - {""}
    if len(roles) > 1:
        return "different_value_roles"
    keys = [physical_key(item) for item in items]
    if any(key is None for key in keys):
        return "physical_identity_unresolved"
    if len(set(keys)) > 1:
        # Разные локальные элементы разных представлений ещё не доказывают разные объекты.
        if len({item.page for item in items}) > 1 and any(
            key[0] == "page_element" for key in keys
        ):
            return "physical_identity_unresolved"
        return "different_physical_objects"
    return "same_physical_object"


def _point(line, end):
    return float(line["x" + str(end)]), float(line["y" + str(end)])


def valid_box(box):
    """Неполная геометрия означает ограничение привязки, а не ошибку всего задания."""
    return (
        isinstance(box, dict)
        and all(
            isinstance(box.get(key), (int, float))
            and not isinstance(box[key], bool)
            and math.isfinite(box[key])
            and 0 <= box[key] <= 1000
            for key in ("x_min", "y_min", "x_max", "y_max")
        )
        and box["x_min"] < box["x_max"]
        and box["y_min"] < box["y_max"]
    )


def valid_line(line):
    """Принимает только конечные нормализованные прямые пути PDF."""
    return (
        isinstance(line, dict)
        and isinstance(line.get("id"), str)
        and bool(line["id"])
        and all(
            isinstance(line.get(key), (int, float))
            and not isinstance(line[key], bool)
            and math.isfinite(line[key])
            and 0 <= line[key] <= 1000
            for key in ("x1", "y1", "x2", "y2")
        )
        and isinstance(line.get("width"), (int, float))
        and math.isfinite(line["width"])
        and line["width"] >= 0
    )


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _on_line(point, line, tolerance=0.6):
    a, b = _point(line, 1), _point(line, 2)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return False
    position = max(
        0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_squared)
    )
    return _distance(point, (a[0] + position * dx, a[1] + position * dy)) <= tolerance


def _inside(point, region, pad=0):
    return (
        region["x_min"] - pad <= point[0] <= region["x_max"] + pad
        and region["y_min"] - pad <= point[1] <= region["y_max"] + pad
    )


def _crosses(left, right):
    """Пересечение внутренностей не превращается в узел сети."""
    a, b, c, d = _point(left, 1), _point(left, 2), _point(right, 1), _point(right, 2)

    def side(p, q, r):
        """Различает стороны прямой для проверки пересечения внутренностей."""
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return (
        side(a, b, c) * side(a, b, d) < -0.01 and side(c, d, a) * side(c, d, b) < -0.01
    )


class PdfLineIndex:
    """Пространственный индекс прямых PDF без построения графа инженерных сетей."""

    def __init__(self, lines):
        """Кеширует проверенные пути одной страницы и ячейки их областей."""
        self.lines = tuple(line for line in lines if valid_line(line))
        self.cells = {}
        for index, line in enumerate(self.lines):
            for cell in self._cells(self.bounds(line)):
                self.cells.setdefault(cell, set()).add(index)

    @staticmethod
    def bounds(line):
        """Возвращает область прямого пути, включая горизонтальные и вертикальные линии."""
        return {
            "x_min": min(line["x1"], line["x2"]),
            "y_min": min(line["y1"], line["y2"]),
            "x_max": max(line["x1"], line["x2"]),
            "y_max": max(line["y1"], line["y2"]),
        }

    @staticmethod
    def _cells(box, pad=0):
        """Перечисляет все затронутые ячейки нормализованной страницы."""
        for x in range(
            max(0, int((box["x_min"] - pad) // 20)),
            min(50, int((box["x_max"] + pad) // 20)) + 1,
        ):
            for y in range(
                max(0, int((box["y_min"] - pad) // 20)),
                min(50, int((box["y_max"] + pad) // 20)) + 1,
            ):
                yield x, y

    def near_box(self, box, pad=0):
        """Возвращает все возможные пересечения, не выбирая ближайший путь."""
        indices = set()
        for cell in self._cells(box, pad):
            indices.update(self.cells.get(cell, ()))
        return tuple(self.lines[index] for index in sorted(indices))

    def at(self, point):
        """Ищет возможные связи у проверяемого конца выноски."""
        return self.near_box(
            {
                "x_min": point[0],
                "x_max": point[0],
                "y_min": point[1],
                "y_max": point[1],
            },
            0.6,
        )


def bind_graphical_fact(
    fact, *, page, text_words=(), graphic_lines=(), line_index=None
):
    """Проверяет цепочку выноски на существующей геометрии без новых inference.

    Остановки: пересечение, несколько продолжений, одинаковые подписи, отсутствие
    отличимого элемента. Связь с ближайшей линией никогда не подтверждается.
    """
    if not geometric_property(fact):
        return fact
    binding = {
        "method": "pdf_vector_chain",
        "status": "unresolved",
        "reason": "label_not_localized",
        "page": page,
    }
    # Область модели должна содержать буквальное исходное значение в PDF-слое.
    from pdrd_analysis_service.domain.evidence_audit import literal_contains

    regions = fact.visual_regions
    matches = []
    for word in text_words:
        box = word.get("bbox", {})
        if not valid_box(box):
            continue
        center = ((box["x_min"] + box["x_max"]) / 2, (box["y_min"] + box["y_max"]) / 2)
        word_text = str(word.get("text", ""))
        value_matches = literal_contains(word_text, fact.value_raw) or (
            bool(fact.unit_raw)
            and literal_contains(word_text, fact.value_raw + fact.unit_raw)
        )
        same_label = value_matches and any(
            literal_contains(str(tag.get("text", "")), fact.identifier)
            and tag.get("block_no") == word.get("block_no")
            for tag in text_words
        )
        if value_matches and same_label and any(_inside(center, r) for r in regions):
            matches.append(box)
    binding["label_matches"] = len(matches)
    if len(matches) != 1:
        if matches:
            binding.update(status="ambiguous", reason="repeated_value_in_region")
        return replace(fact, geometry_binding=binding)
    box = matches[0]
    binding["label_region"] = dict(box)
    geometry = line_index if line_index is not None else PdfLineIndex(graphic_lines)
    leaders = [
        (line, end)
        for line in geometry.near_box(box, 1.5)
        for end in (1, 2)
        if _inside(_point(line, end), box, 1.5)
        and not _inside(_point(line, 3 - end), box, 1.5)
    ]
    paths = []
    for leader, start in leaders:
        chain, current, point = [leader], leader, _point(leader, 3 - start)
        for _ in range(8):
            targets = [
                line
                for line in geometry.at(point)
                if line not in chain
                and _on_line(point, line)
                and line.get("color") == leader.get("color")
                and float(line.get("width", 0)) > float(leader.get("width", 0)) * 1.4
            ]
            if len(targets) == 1:
                target = targets[0]
                crossings = any(
                    _crosses(part, line)
                    for part in chain
                    for line in geometry.near_box(geometry.bounds(part))
                    if line not in chain
                    and line != target
                    and float(line.get("width", 0)) >= float(leader.get("width", 0))
                )
                if not crossings:
                    paths.append((chain, target))
                break
            if len(targets) > 1:
                break
            next_lines = [
                (line, end)
                for line in geometry.at(point)
                if line not in chain
                for end in (1, 2)
                if _distance(point, _point(line, end)) <= 0.6
                and line.get("color") == current.get("color")
                and abs(float(line.get("width", 0)) - float(leader.get("width", 0)))
                < 0.01
            ]
            if len(next_lines) != 1:
                break
            current, end = next_lines[0]
            chain.append(current)
            point = _point(current, 3 - end)
    if len(paths) != 1:
        binding.update(
            status="ambiguous" if leaders else "unresolved",
            reason="leader_chain_not_unique" if leaders else "leader_unavailable",
        )
    else:
        chain, target = paths[0]
        coordinates = sorted((_point(target, 1), _point(target, 2)))
        element = hashlib.sha256(repr((page, coordinates)).encode()).hexdigest()[:20]
        binding.update(
            status="bound",
            reason="unique_connected_leader",
            element_id="pdf-" + element,
            leader_lines=[dict(line) for line in chain],
            target_lines=[dict(target)],
        )
    return replace(fact, geometry_binding=binding)
