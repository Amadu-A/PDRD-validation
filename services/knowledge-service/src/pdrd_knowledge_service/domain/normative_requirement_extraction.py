# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_requirement_extraction.py

"""Воспроизводимая атомизация СП, ГОСТ и нормативных текстов со статьями.

Извлечение сохраняет буквальный текст, физические страницы и исходные
смещения. Для документов со структурой статей используется отдельный
консервативный режим: слишком длинные участки делятся без потери текста,
но не считаются автоматически проверенными нормативными требованиями.
"""

import re
from collections.abc import Iterator
from itertools import pairwise
from uuid import UUID

from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage
from pdrd_knowledge_service.domain.normative_requirements import (
    EXTRACTOR_VERSION,
    NormativeRequirement,
    NormativeSourceFragment,
    stable_requirement_id,
)

_CLAUSE = re.compile(
    r"(?m)^[ \t]*(?P<clause>(?:\d+|[А-ЯA-Z])(?:\.\d+)+)"
    r"(?:[ \t]+(?P<letter>[а-яa-z])\))?"
    r"(?:[ \t]+|\r?\n[ \t]*)"
)
_ARTICLE = re.compile(
    r"(?mi)^[ \t]*Статья[ \t]+(?P<number>\d+(?:\.\d+)?)"
    r"(?=[ \t.:)\r\n]|$)"
)
_CODE = re.compile(
    r"\b(?:СП|ГОСТ(?:\s+Р)?|СНиП)\s+\d+(?:[.\-–]\d+)*",
    re.I,
)
_MANDATORY = re.compile(
    r"\b(?:долж(?:ен|на|но|ны)|следует|необходимо|надлежит|"
    r"предусматривают|принимают|подлеж(?:ат|ит))\b"
    r"|\bне\s+допускается\b|\bопределяют\s+расч[её]том\b",
    re.I,
)
_ADVISORY = re.compile(
    r"рекомендуется|рекомендован|как правило|целесообразно",
    re.I,
)
_PERMISSION = re.compile(
    r"(?<!не )\bдопускается\b|разрешается|допустимо",
    re.I,
)
_LIST_ITEM = re.compile(r"(?m)(?:^[ \t]*|(?<=[;:])[ \t]*)([а-яa-z])\)\s+")
_NON_PRESCRIPTIVE_CONTEXT = re.compile(
    r"термин\w*\s+и\s+определени|нормативн\w*\s+ссылк|"
    r"библиограф|справочн\w*",
    re.I,
)

# Ограничивает исходный текст одного фрагмента в режиме статей.
# Это не лимит JSONB и не разрешение усекать исходный документ.
_LEGAL_SOURCE_SEGMENT_CHARS = 8000


def normative_document_identity(
    pages: tuple[NormativeTextPage, ...],
) -> tuple[str, str]:
    """Извлекает обозначение СП/ГОСТ из документа, не используя имя файла."""
    text = "\n".join(page.text for page in pages[:5])
    matches = tuple(_CODE.finditer(text))
    if not matches:
        return "", ""
    code = re.sub(r"\s+", " ", matches[0][0]).upper().replace("–", "-")
    year = re.search(r"[.\-](\d{4})$", code)
    return code, year[1] if year else ""


def _fragments(
    ranges: tuple[tuple[int, int, NormativeTextPage], ...],
    start: int,
    end: int,
) -> tuple[NormativeSourceFragment, ...]:
    """Проецирует буквальный исходный диапазон на физические страницы PDF."""
    return tuple(
        NormativeSourceFragment(
            page.page_number,
            max(start, left) - left,
            min(end, right) - left,
            page.text[max(start, left) - left : min(end, right) - left],
        )
        for left, right, page in ranges
        if max(start, left) < min(end, right) or (left == right and start <= left < end)
    )


def _matches(pattern: str, text: str) -> tuple[str, ...]:
    """Возвращает буквальные уникальные совпадения без свободного пересказа."""
    return tuple(
        dict.fromkeys(match[0].strip() for match in re.finditer(pattern, text, re.I))
    )


def _verification_type(text: str, clause: str) -> str:
    """Классифицирует форму проверки, не подтверждая применимость нормы."""
    text = re.sub(r"\s+", " ", text)

    if re.search(r"таблиц\w*\s+\d", clause, re.I):
        return "table"
    if re.search(r"формул|[=∑√]|\b[А-ЯA-Za-z]\s*=", text, re.I):
        return "formula"
    if re.search(
        r"расч[её]т(?:ом|ов|а|ы)?(?!\w)|"
        r"обоснов\w*.*расч[её]т|"
        r"определя\w*\s+исходя\s+из\s+расч[её]т",
        text,
        re.I,
    ):
        return "calculation_required"
    if re.search(
        r"классифиц|относ\w*\s+к\s+категор|категори\w*\s+по",
        text,
        re.I,
    ):
        return "classification"
    if re.search(
        r"не\s+(?:менее|более)\s+\d|от\s+\d.*до\s+\d|[<>≤≥]",
        text,
        re.I,
    ):
        return "numeric_limit"
    if re.search(
        r"в состав|состав\w*\s+(?:оборудован|систем)|"
        r"комплект|резервн\w*\s+насос",
        text,
        re.I,
    ):
        return "system_composition"
    if re.search(
        r"документац|черт[её]ж|ведомост|спецификац|"
        r"нумерац|основн\w*\s+надпис",
        text,
        re.I,
    ):
        return "documentation"
    if re.search(r"взамен|вместо|альтернатив", text, re.I):
        return "alternative"
    if _PERMISSION.search(text):
        return "alternative"
    if re.search(r"\bесли\b|\bпри\b", text, re.I):
        return "conditional"

    return "action" if _MANDATORY.search(text) else "unsupported"


def _atoms(text: str) -> tuple[str, ...]:
    """Делит явные предписания, не отделяя формулы и таблицы от условий."""
    if re.search(r"таблиц\w*\s+\d|формул|[=∑√]", text, re.I):
        return (text,)

    markers = tuple(_LIST_ITEM.finditer(text))
    if markers:
        return tuple(
            text[
                marker.start() : markers[index + 1].start()
                if index + 1 < len(markers)
                else len(text)
            ].strip()
            for index, marker in enumerate(markers)
        )

    parts = tuple(
        value.strip()
        for value in re.split(
            r";|(?<=[.!?])\s+(?=[А-ЯA-Z])",
            text,
        )
        if value.strip()
    )
    return parts or (text,)


def _legal_source_segments(
    text: str,
    ranges: tuple[tuple[int, int, NormativeTextPage], ...],
    start: int,
    end: int,
) -> Iterator[tuple[int, int, bool]]:
    """Без потерь делит длинный участок юридического текста на малые диапазоны.

    Предпочитает границу физической страницы, затем перевод строки.
    Если структурной границы нет, используется точное символьное смещение.
    Все куски исходного диапазона остаются последовательными и непересекающимися.

    Последний признак показывает искусственное деление: такой источник нельзя
    автоматически интерпретировать как самостоятельный нормативный пункт.
    """
    if end - start <= _LEGAL_SOURCE_SEGMENT_CHARS:
        yield start, end, False
        return

    page_starts = tuple(left for left, _, _ in ranges if start < left < end)
    current = start

    while current < end:
        boundary = min(current + _LEGAL_SOURCE_SEGMENT_CHARS, end)

        if boundary < end:
            page_boundary = next(
                (
                    candidate
                    for candidate in reversed(page_starts)
                    if current < candidate <= boundary
                ),
                None,
            )

            if page_boundary is not None:
                boundary = page_boundary
            else:
                newline = text.rfind(
                    "\n",
                    current + 1,
                    boundary,
                )
                if newline >= 0:
                    boundary = newline + 1

        if boundary <= current:
            raise ValueError(
                "Невозможно продвинуть границу исходного нормативного текста."
            )

        yield current, boundary, True
        current = boundary


def extract_normative_requirements(
    *,
    document_id: UUID,
    source_sha256: str,
    source_file: str,
    pages: tuple[NormativeTextPage, ...],
    extractor_version: str = EXTRACTOR_VERSION,
) -> Iterator[NormativeRequirement]:
    """Извлекает атомы с буквальными источниками и физическими страницами.

    Для СП/ГОСТ сохраняется существующий алгоритм нумерованных пунктов.
    Если таких пунктов нет, а документ содержит заголовки статей,
    используются границы статей. Неопределённые большие промежутки
    дополнительно делятся без усечения текста и маркируются needs_review.
    """
    if len({page.page_number for page in pages}) != len(pages) or any(
        page.page_number < 1 for page in pages
    ):
        raise ValueError(
            "Физические страницы нормативного документа должны быть уникальными."
        )

    ordered = tuple(sorted(pages, key=lambda page: page.page_number))

    ranges = []
    offset = 0
    for page in ordered:
        ranges.append((offset, offset + len(page.text), page))
        offset += len(page.text) + 1

    page_ranges = tuple(ranges)
    text = "\n".join(page.text for page in ordered)
    document_code, edition = normative_document_identity(ordered)

    clauses = []
    for match in _CLAUSE.finditer(text):
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", match["clause"]):
            continue

        prefix = text[max(0, match.start() - 120) : match.start()].rstrip()

        if re.search(
            r"(?:\bСП|\bГОСТ(?:\s+Р)?|\bСНиП|"
            r"\bпункт(?:ом|у|а|е|ы|ов)?|\bпп?\.)$",
            prefix,
            re.I,
        ):
            continue

        if clauses and re.search(
            r"(?mi)^\s*таблица\s+\d",
            text[clauses[-1].start() : match.start()],
        ):
            line_end = text.find("\n", match.end())
            body = text[match.end() : line_end if line_end >= 0 else len(text)]
            if not re.search(r"[А-Яа-яA-Za-z]{3,}", body):
                continue

        clauses.append(match)

    article_mode = False

    if clauses:
        boundaries = [
            (
                match.start(),
                match["clause"],
                match["letter"] or "",
            )
            for match in clauses
        ]
    else:
        articles = tuple(_ARTICLE.finditer(text))
        article_mode = bool(articles)

        boundaries = [
            (
                match.start(),
                match["number"],
                "",
            )
            for match in articles
        ]

    if not boundaries or boundaries[0][0] > 0:
        boundaries.insert(0, (0, "", ""))

    headings = tuple(
        re.finditer(
            r"(?m)^[ \t]*(?:\d+[ \t]+[А-ЯA-Z][^\n]{2,200}"
            r"|Приложение[ \t]+[А-ЯA-Z][ \t]*"
            r"(?:\r?\n[ \t]*)?(?:\([^\n)]{1,100}\))?)[ \t]*$",
            text,
        )
    )

    for index, (start, clause_number, subclause) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)

        if article_mode:
            source_ranges = _legal_source_segments(
                text,
                page_ranges,
                start,
                end,
            )
        else:
            source_ranges = ((start, end, False),)

        for segment_index, (
            segment_start,
            segment_end,
            is_segmented,
        ) in enumerate(source_ranges):
            source = text[segment_start:segment_end]
            if not source.strip():
                continue

            fragments = _fragments(
                page_ranges,
                segment_start,
                segment_end,
            )
            if not fragments:
                continue

            # После искусственного разрыва нельзя утверждать, что весь
            # следующий текст принадлежит последней найденной статье.
            effective_clause = (
                "" if is_segmented and segment_index > 0 else clause_number
            )

            context = tuple(
                heading[0].strip()
                for heading in headings
                if heading.start() <= segment_start
            )[-4:]

            conditions = _matches(
                r"(?:\bпри\b|\bесли\b|в случае)[^;.!?]+",
                source,
            )
            exceptions = _matches(
                r"(?:за исключением|кроме|"
                r"не распространяется|не требуется)[^;.!?]+",
                source,
            )
            dependencies = _matches(
                r"\b(?:пункт[а-я]*|пп?\.)\s+\d+(?:\.\d+)*"
                r"|\bтаблиц[а-я]*\s+(?:(?-i:[А-ЯA-Z])\.)?"
                r"\d+(?:\.\d+)*"
                r"|\bприложени[а-я]*\s+(?-i:[А-ЯA-Z])"
                r"(?:\.\d+)*(?![а-яa-z])"
                r"|\b(?:по\s+формуле|сноск[а-я]*)\s+"
                r"\(?\d+(?:\.\d+)*\)?"
                r"|\b(?:СП|ГОСТ(?:\s+Р)?|СНиП)\s+"
                r"\d+(?:[.\-–]\d+)+",
                source,
            )

            atoms = _atoms(source)

            for atom_index, atom in enumerate(atoms, 1):
                marker = re.match(
                    r"([а-яa-z])\)",
                    atom,
                )
                atom_subclause = marker[1] if marker else subclause

                first_marker = _LIST_ITEM.search(source)
                parent_header = (
                    source[: first_marker.start()].strip() if first_marker else ""
                )

                modality_text = re.sub(
                    r"\s+",
                    " ",
                    atom,
                )

                mandatory, advisory, permission = (
                    bool(pattern.search(modality_text))
                    for pattern in (
                        _MANDATORY,
                        _ADVISORY,
                        _PERMISSION,
                    )
                )

                inherited = bool(
                    parent_header
                    and marker
                    and not (mandatory or advisory or permission)
                    and parent_header.endswith(":")
                )

                if inherited:
                    mandatory, advisory, permission = (
                        bool(pattern.search(re.sub(r"\s+", " ", parent_header)))
                        for pattern in (
                            _MANDATORY,
                            _ADVISORY,
                            _PERMISSION,
                        )
                    )

                modality = (
                    "advisory"
                    if advisory and not mandatory
                    else "mandatory"
                    if mandatory and not advisory
                    else "permitted"
                    if permission and not mandatory
                    else "unknown"
                    if mandatory or advisory
                    else "informative"
                )

                verification = _verification_type(
                    atom,
                    source,
                )

                limitations = []

                if context and _NON_PRESCRIPTIVE_CONTEXT.search(context[-1]):
                    modality = "informative"
                    limitations.append("non_prescriptive_section")

                if inherited:
                    limitations.append("inherited_prescription_requires_review")

                if article_mode:
                    limitations.append("legal_article_requires_review")

                if is_segmented:
                    limitations.append("source_segment_requires_review")

                if not effective_clause:
                    limitations.append("clause_binding_unavailable")

                if any(
                    second.page - first.page != 1
                    for first, second in pairwise(fragments)
                ):
                    limitations.append("source_pages_missing")

                if len(atoms) > 1 and (conditions or exceptions):
                    limitations.append("shared_clause_conditions_require_review")

                if not document_code or not edition:
                    limitations.append("document_edition_unidentified")

                if not re.search(
                    r"[.!?;:]\s*$",
                    source,
                ):
                    limitations.append("clause_end_unverified")

                if verification in {
                    "table",
                    "formula",
                    "classification",
                    "conditional",
                    "alternative",
                    "unsupported",
                }:
                    limitations.append("structured_interpretation_required")

                if modality != "mandatory":
                    limitations.append("not_an_unambiguous_mandatory_prescription")

                if re.search(
                    r"\bи\b.*\b(?:долж|следует)"
                    r"|\bлибо\b|\bили\b",
                    atom,
                    re.I,
                ):
                    limitations.append("compound_prescription_requires_review")

                parameters = _matches(
                    r"расход\w*|напор\w*|давлени\w*|"
                    r"диаметр\w*|объ[её]м\w*|"
                    r"продолжительност\w*|категори\w*|"
                    r"температур\w*|количеств\w*|"
                    r"высот\w*|площад\w*",
                    source,
                )

                subject = re.search(
                    r"(?:насос\w*|резервуар\w*|"
                    r"гидрант\w*|трубопровод\w*|"
                    r"здани\w*|сооружени\w*|"
                    r"помещени\w*|систем\w*|"
                    r"документаци\w*)",
                    atom,
                    re.I,
                )

                if subject is None:
                    limitations.append("object_applicability_requires_review")

                point_id = stable_requirement_id(
                    str(document_id),
                    source_sha256,
                    extractor_version,
                    document_code,
                    edition,
                    effective_clause,
                    atom_subclause,
                    fragments[0].page,
                    segment_start,
                    atom_index,
                    atom,
                )

                yield NormativeRequirement(
                    requirement_id="N-R-" + point_id,
                    point_id=point_id,
                    document_id=str(document_id),
                    source_sha256=source_sha256,
                    source_file=source_file,
                    document_code=document_code,
                    edition=edition,
                    clause=effective_clause,
                    subclause=atom_subclause,
                    page=fragments[0].page,
                    source_text=source,
                    text=atom,
                    object_type=(subject[0] if subject else ""),
                    applicability_conditions=conditions,
                    exceptions=exceptions,
                    required_parameters=parameters,
                    verification_type=verification,
                    normative_dependencies=dependencies,
                    extraction_status=("needs_review" if limitations else "extracted"),
                    modality=modality,
                    source_fragments=fragments,
                    context=((*context, parent_header) if parent_header else context),
                    limitations=tuple(limitations),
                )
