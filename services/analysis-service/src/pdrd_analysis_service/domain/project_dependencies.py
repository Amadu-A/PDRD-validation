# services/analysis-service/src/pdrd_analysis_service/domain/project_dependencies.py

"""Явные ссылки, числовые равенства и адресные соединения из исходного текста."""

import re

from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    document_fact_from_mapping,
    normalize_label,
)

_TARGET = (
    r"(?P<kind>таблиц[ауые]|приложени[еяюи]|расч[её]т[ауые]?)\s+"
    r"(?P<key>\d+[\w.-]*|(?-i:[A-ZА-Я])\d+(?:[.-]\d+)*|(?-i:[A-ZА-Я]))(?!\w)"
)
_REFERENCE = re.compile(r"(?<!\w)(?:см\.?|согласно|по|в)\s+" + _TARGET, re.I)
_DEFINITION = re.compile(r"^\s*" + _TARGET + r"(?:\s|[.:—–-]|$)", re.I | re.M)
_EQUATION = re.compile(
    r"(?<![\w.])(?P<expression>\d+(?:[.,]\d+)?(?:\s*[+*/×·-]\s*\d+(?:[.,]\d+)?)+)\s*=\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)(?:\s*(?P<unit>м[³3]/ч|л/с|м[³3]|кПа|МПа|кВт|мм|шт\.?|м))?(?![\w.,])"
)
_CONNECTION = re.compile(
    r"(?P<source>[\w.-]+/[\w.:-]+)\s*(?:->|→)\s*(?P<target>[\w.-]+/[\w.:-]+)"
)


def explicit_project_dependencies(text: str, page: int) -> tuple[DocumentFact, ...]:
    """Не читает чертёж заново и не восстанавливает скрытые формулы или кабельные связи."""
    facts = []
    for pattern, definition in ((_REFERENCE, False), (_DEFINITION, True)):
        for match in pattern.finditer(text):
            if not definition and re.match(
                r"\s*(?:из\s+)?(?:СП|ГОСТ|СНиП|ПУЭ)\b", text[match.end() :], re.I
            ):
                continue
            kind = normalize_label(match["kind"])
            category = (
                "таблица"
                if kind.startswith("таблиц")
                else "приложение"
                if kind.startswith("приложени")
                else "расчет"
            )
            target = category + " " + normalize_label(match["key"]).rstrip(".")
            facts.append(
                _fact(
                    page,
                    evidence=match[0].strip(),
                    subject_type="document_reference",
                    identifier=target,
                    property_type="document_definition"
                    if definition
                    else "document_reference",
                    property_name="Определение ссылки"
                    if definition
                    else "Внутренняя ссылка",
                    value_raw=target,
                    relation="defines_reference" if definition else "references",
                )
            )
    for match in _EQUATION.finditer(text):
        facts.append(
            _fact(
                page,
                evidence=match[0],
                subject_type="calculation",
                identifier=f"equation-p{page}-{match.start()}",
                property_type="arithmetic_expression",
                property_name="Числовое равенство",
                value_raw=match["value"],
                unit_raw=match["unit"] or "",
                relation="equation",
                column_label=match["expression"],
            )
        )
    for match in _CONNECTION.finditer(text):
        facts.append(
            _fact(
                page,
                evidence=match[0],
                subject_type="terminal",
                identifier=match["source"],
                property_type="connection",
                property_name="Соединение",
                value_raw=match["target"],
                relation="connected_to",
            )
        )
    return tuple(fact for fact in facts if fact is not None)


def _fact(page, *, evidence, **values):
    """Назначает происхождение только дословно прочитанной записи."""
    return document_fact_from_mapping(
        {
            "kind": "relationship",
            "evidence_text": evidence,
            "source_pages": [page],
            "extraction_method": "pdf_text",
            **values,
        }
    )
