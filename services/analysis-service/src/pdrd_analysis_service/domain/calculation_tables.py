# services/analysis-service/src/pdrd_analysis_service/domain/calculation_tables.py

"""Ячейки расчётной таблицы по уже извлечённым PDF-словам, без чтения изображения моделью."""

import hashlib
import re
from dataclasses import dataclass, replace
from itertools import pairwise

from pdrd_analysis_service.domain.document_context import (
    document_fact_from_mapping,
    normalize_label,
)

_UNITS = {
    "м3/сут": "м³/сут",
    "м³/сут": "м³/сут",
    "м3/ч": "м³/ч",
    "м³/ч": "м³/ч",
    "л/с": "л/с",
}
_VALUE = re.compile(r"\d+(?:[.,]\d+)?(?:[хx×*]\d+(?:[.,]\d+)?)?")
_SUM_RULE = re.compile(r"сумм\w*\s+(?:всех\s+)?строк", re.I)


def column_unit_key(value):
    """Сравнивает написание одной единицы, не приравнивая часовой расход секундному."""
    value = normalize_label(value).replace(" ", "").replace("³", "3")
    return {"m3/h": "м3/ч", "м3/час": "м3/ч", "m3/day": "м3/сут", "l/s": "л/с"}.get(
        value, value
    )


@dataclass(frozen=True)
class _Word:
    text: str
    box: tuple[float, float, float, float]

    @property
    def x(self):
        """Горизонтальная середина исходного слова."""
        return (self.box[0] + self.box[2]) / 2

    @property
    def y(self):
        """Вертикальная середина исходного слова."""
        return (self.box[1] + self.box[3]) / 2


def _words(raw_words):
    result = []
    for raw in raw_words:
        try:
            box = tuple(
                float(raw["bbox"][key]) for key in ("x_min", "y_min", "x_max", "y_max")
            )
            if not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
                continue
            result.append(_Word(str(raw["text"]), box))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda word: (word.y, word.x))


def _text(words):
    return " ".join(
        word.text
        for word in sorted(words, key=lambda word: (round(word.y / 3), word.x))
    )


def _region(words, role, page):
    return {
        "page": page,
        "role": role,
        "text": _text(words),
        "bbox": {
            "x_min": min(word.box[0] for word in words),
            "y_min": min(word.box[1] for word in words),
            "x_max": max(word.box[2] for word in words),
            "y_max": max(word.box[3] for word in words),
        },
    }


def calculation_tables_by_page(pages, *, link_continuations=True):
    """Связывает лишь явное продолжение соседнего листа с тем же номером и колонками.

    Исходные row/item ID и координаты не меняются. Одинаковый заголовок без маркера
    продолжения не объединяет разные таблицы; отсутствие заголовка не восстанавливается.
    """
    previous = {}
    result = {}
    for page, words in pages:
        facts = calculation_table_facts(words, page)
        tables = {}
        for fact in facts:
            tables.setdefault(fact.table_id, []).append(fact)
        linked = []
        for table_id, members in tables.items():
            number = re.search(
                r"таблиц[аы]\s*(\d+(?:\.\d+)*)", members[0].table_title, re.I
            )
            key = (
                (number[1], tuple(sorted({fact.column_label for fact in members})))
                if number
                else None
            )
            predecessor = previous.get(key)
            if (
                link_continuations
                and members[0].continuation_marker
                and predecessor
                and predecessor[0] == page - 1
            ):
                table_id = predecessor[1]
            linked.extend(replace(fact, table_id=table_id) for fact in members)
            if key:
                previous[key] = (page, table_id)
        result[page] = tuple(linked)
    return result


def calculation_table_facts(raw_words, page: int):
    """Сохраняет буквальные колонки, пустые ячейки и явно подписанный режим; итог не объявляет суммой.

    Разбор допустим при нескольких однозначных единицах на одной линии заголовка
    и явном столбце наименования. Без геометрии прежние факты остаются нетронутыми.
    Полнота таблицы и правило суммирования этим извлечением не устанавливаются.
    """
    words = _words(raw_words)
    headers = [word for word in words if column_unit_key(word.text) in _UNITS]
    used, result = set(), []
    for header in headers:
        if header in used:
            continue
        height = header.box[3] - header.box[1]
        columns = sorted(
            (word for word in headers if abs(word.y - header.y) < height * 0.7),
            key=lambda word: word.x,
        )
        if len(columns) < 2 or len(
            {column_unit_key(word.text) for word in columns}
        ) != len(columns):
            continue
        used.update(columns)
        near = [
            word
            for word in words
            if header.y - height * 9 < word.y <= header.y + height
        ]
        names = [
            word
            for word in near
            if re.fullmatch(r"наименование|потребител[ья]|потребитель", word.text, re.I)
        ]
        if not names:
            continue
        mode = [
            word
            for word in near
            if word.y > header.y - height * 2
            and re.fullmatch(r"режим|водопотребления", word.text, re.I)
        ]
        label_right = min(
            (
                word.box[0]
                for word in near
                if word.box[3] > header.box[1] - height
                and names[0].x < word.x < columns[0].x
                and word not in names
                and word not in mode
            ),
            default=columns[0].box[0],
        )
        mode_left = (
            min((word.box[0] for word in mode), default=columns[0].box[0]) - height
        )
        centres = [word.x for word in columns]
        bounds = [
            centres[0] - (centres[1] - centres[0]) / 2,
            *((a + b) / 2 for a, b in pairwise(centres)),
            centres[-1] + (centres[-1] - centres[-2]) / 2,
        ]
        title_words = [word for word in near if word.y < header.y - height * 4]
        title = _text(title_words)
        table_id = (
            f"calc-p{page}-"
            + hashlib.sha256(
                (title + str(tuple(round(x) for x in centres))).encode()
            ).hexdigest()[:12]
        )
        body = [word for word in words if word.y > header.y + height * 2]
        stop = min(
            (
                word.y
                for word in body
                if word.x < bounds[0]
                and sum(
                    other.x >= bounds[0]
                    and other.x < bounds[-1]
                    and abs(other.y - word.y) < height * 0.45
                    and bool(re.search(r"[а-яa-z]", other.text, re.I))
                    for other in body
                )
                >= 2
            ),
            default=1001,
        )
        body = [word for word in body if word.y < stop - height / 2]
        anchors = [
            word
            for word in body
            if bounds[0] <= word.x <= bounds[-1]
            and (_VALUE.fullmatch(word.text) or word.text in {"-", "—", "–"})
        ]
        rows = []
        for word in anchors:
            if not rows or abs(word.y - rows[-1]) > height * 0.7:
                rows.append(word.y)
        headings = []
        for word in body:
            if not (label_right < word.box[0] < mode_left) or not re.search(
                r"[а-яa-z]", word.text, re.I
            ):
                continue
            line = [other for other in body if abs(other.y - word.y) < height * 0.45]
            if (
                any(other.x <= label_right or other.x >= bounds[0] for other in line)
                or max(other.box[2] for other in line) <= mode_left
            ):
                continue
            heading = (word.y, _text(line))
            if heading not in headings:
                headings.append(heading)
        for index, y in enumerate(rows):
            top = (rows[index - 1] + y) / 2 if index else header.y + height * 1.5
            bottom = (
                (rows[index + 1] + y) / 2 if index + 1 < len(rows) else y + height * 1.5
            )
            before = [(hy, label) for hy, label in headings if hy < y]
            after = [hy for hy, _ in headings if hy > y]
            group = max(before, default=(header.y, ""))[1]
            if before:
                top = max(top, max(hy for hy, _ in before) + height / 2)
            if after:
                bottom = min(bottom, min(after) - height / 2)
            row_words = [word for word in body if top <= word.y < bottom]
            prefix = [word for word in row_words if word.x < bounds[0]]
            total = bool(re.search(r"\bитого\b|\bвсего\b", _text(prefix), re.I))
            labels = (
                prefix if total else [word for word in prefix if word.x < label_right]
            )
            label = _text(labels)
            if not label or not re.search(r"[а-яa-z]", label, re.I):
                continue
            modes = (
                []
                if total or not mode
                else [word for word in prefix if mode_left <= word.x < bounds[0]]
            )
            mode_text = _text(modes)
            row_id = f"{table_id}:row-{index}"
            for column, (left, right) in enumerate(pairwise(bounds)):
                cell = [word for word in row_words if left <= word.x < right]
                raw = _text(cell)
                if raw and raw not in {"-", "—", "–"} and not _VALUE.fullmatch(raw):
                    continue
                regions = [
                    _region(near, "table_header", page),
                    _region((columns[column],), "column_header", page),
                    _region(labels, "row_header", page),
                ]
                if modes:
                    regions.append(_region(modes, "operating_mode", page))
                if cell:
                    regions.append(_region(cell, "cell", page))
                value = raw if _VALUE.fullmatch(raw) else ""
                unit = _UNITS[column_unit_key(columns[column].text)]
                name = {
                    "м³/сут": "Суточный расход воды",
                    "м³/ч": "Часовой расход воды",
                    "л/с": "Секундный расход воды",
                }[unit]
                fact = document_fact_from_mapping(
                    {
                        "kind": "parameter",
                        "subject_type": "flow_consumer",
                        "subject_name": label,
                        "identifier": normalize_label(label),
                        "property_type": "parameter",
                        "property_name": name,
                        "value_raw": value,
                        "unit_raw": unit,
                        "evidence_text": _text(row_words)
                        if total and _SUM_RULE.search(label)
                        else raw or label,
                        "calculation_rule": "sum"
                        if total and _SUM_RULE.search(label)
                        else "",
                        "calculation_evidence": _SUM_RULE.search(label)[0]
                        if total and _SUM_RULE.search(label)
                        else "",
                        "table_id": table_id,
                        "table_title": title,
                        "row_label": label,
                        "column_label": columns[column].text,
                        "scope_operating_mode": mode_text,
                        "calculation_group": group,
                        "relation": "table_total" if total else "table_cell",
                        "column_binding": "pdf_geometry",
                        "source_pages": [page],
                        "source_row_ids": [row_id],
                        "source_item_id": f"{row_id}:c{column}",
                        "source_regions": regions,
                        "extraction_method": "pdf_table",
                        "continuation_marker": "Продолжение"
                        if re.search(r"продолжени", title, re.I)
                        else "",
                    }
                )
                if fact:
                    result.append(fact)
    return _explicit_sums(tuple(result), words, page)


def _explicit_sums(facts, words, page):
    """Сохраняет явно заданную сумму и её покрытие; конец не угадывается по последней видимой строке."""
    groups = {}
    for fact in facts:
        groups.setdefault((fact.table_id, fact.calculation_group), []).append(fact)
    updates, closures = {}, []
    for (table_id, group), members in groups.items():
        if not any(fact.calculation_rule == "sum" for fact in members) or any(
            fact.continuation_marker for fact in members
        ):
            continue
        parts = [fact for fact in members if fact.relation == "table_cell"]
        modes = {fact.scope_operating_mode for fact in parts}
        if len(modes) != 1 or not all(modes):
            continue
        mode = next(iter(modes))
        # Многострочная область без сетки не доказывает, что две строки не склеены.
        if any(
            region["bbox"]["y_max"] - region["bbox"]["y_min"] > 1.5 * header_height
            for fact in members
            for header_height in [
                next(
                    region["bbox"]["y_max"] - region["bbox"]["y_min"]
                    for region in fact.source_regions
                    if region["role"] == "column_header"
                )
            ]
            for region in fact.source_regions
            if region["role"] in {"row_header", "operating_mode"}
        ):
            continue
        last_y = max(
            region["bbox"]["y_max"]
            for fact in members
            for region in fact.source_regions
            if region["role"] == "cell"
        )
        end = []
        for word in words:
            if word.y <= last_y or normalize_label(word.text) != "конец":
                continue
            if any(
                last_y < other.y < word.y
                and re.fullmatch(r"таблиц[аы]", normalize_label(other.text))
                for other in words
            ):
                break
            line = [
                other
                for other in words
                if abs(other.y - word.y) < (word.box[3] - word.box[1]) * 0.5
            ]
            if re.match(r"конец\s+таблиц", normalize_label(_text(line))):
                end = line
                break
        if not end:
            continue
        for fact in members:
            if fact.relation == "table_cell" or fact.calculation_rule == "sum":
                updates[fact.source_item_id] = replace(
                    fact,
                    relation="sum_component"
                    if fact.relation == "table_cell"
                    else "sum_total",
                    calculation_group=group or table_id,
                    scope_operating_mode=mode,
                )
        closure = document_fact_from_mapping(
            {
                "kind": "relationship",
                "subject_type": "table",
                "identifier": table_id,
                "property_type": "table_marker",
                "property_name": "Конец таблицы",
                "value_raw": "end",
                "evidence_text": _text(end),
                "table_id": table_id,
                "calculation_group": group or table_id,
                "scope_operating_mode": mode,
                "relation": "table_end",
                "source_pages": [page],
                "source_row_ids": list(
                    dict.fromkeys(
                        row for fact in members for row in fact.source_row_ids
                    )
                ),
                "source_regions": [_region(end, "table_end", page)],
                "extraction_method": "pdf_table",
            }
        )
        if closure:
            closures.append(closure)
    return (*(updates.get(fact.source_item_id, fact) for fact in facts), *closures)
