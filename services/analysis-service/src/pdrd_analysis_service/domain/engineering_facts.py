# services/analysis-service/src/pdrd_analysis_service/domain/engineering_facts.py

"""Дословные инженерные отношения из текста без нормативных догадок и эталонных ответов."""

import re
from dataclasses import replace

from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    document_fact_from_mapping,
)

_PURPOSE = r"(?:водоснабжен(?:ия|ие)|водоотведен(?:ия|ие)|электроснабжен(?:ия|ие)|теплоснабжен(?:ия|ие))"
_TAG = r"[A-ZА-Я]{1,3}\d{1,3}(?:-\d{1,3})?"
_DECLARATION = re.compile(
    rf"(?P<purpose>{_PURPOSE})\s*\(?\s*(?P<tag>{_TAG})(?![\w\d])", re.I
)
_LEGEND = re.compile(
    rf"(?<!\w)(?P<tag>{_TAG})\s+(?:сеть\s+)?(?:[а-я-]+\s+){{0,3}}(?P<purpose>{_PURPOSE})",
    re.I,
)
_DESIGN_PARAMETER = re.compile(
    r"(?P<name>расч[её]тн\w*\s+расход|(?:требуемый|гарантированный)\s+напор|потер\w*\s+напора|"
    r"скорост\w*|об[ъь][её]м\w*|запас\w*\s+воды)"
    r"(?P<context>[^.;:]{0,90}?)"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>м[³3]/(?:ч|сут)|л/с|м/с|м[³3]|мм|м)(?!\w)",
    re.I,
)


def explicit_system_purposes(text: str) -> tuple[DocumentFact, ...]:
    """Извлекает только прямо подписанное назначение тега; значение тега не угадывает."""
    content = re.sub(r"\s+", " ", text)
    found = []
    seen = set()
    for pattern in (_DECLARATION, _LEGEND):
        for match in pattern.finditer(content):
            tag = match["tag"].upper()
            purpose = match["purpose"].casefold()
            purpose = re.sub(r"(?:ия|ие)$", "ие", purpose)
            key = (tag, purpose)
            if key in seen:
                continue
            seen.add(key)
            fact = document_fact_from_mapping(
                {
                    "kind": "relationship",
                    "subject_type": "system",
                    "subject_name": f"Система {tag}",
                    "identifier": tag,
                    "property_type": "relationship",
                    "property_name": "Назначение системы",
                    "value_raw": purpose,
                    "evidence_text": match[0],
                    "extraction_method": "pdf_text",
                }
            )
            if fact is not None:
                found.append(fact)
    return tuple(found)


def explicit_design_parameters(text: str) -> tuple[DocumentFact, ...]:
    """Сохраняет явно принятые расходы, напоры, скорости и объёмы как вопросы обоснования, не как нарушения."""
    content = re.sub(r"\s+", " ", text)
    facts = []
    for match in _DESIGN_PARAMETER.finditer(content):
        context = re.sub(
            r"\b(?:принят\w*|составля\w*|равн\w*|объ[её]мом)\b",
            "",
            match["context"],
            flags=re.I,
        ).strip()
        prefix = content[max(0, match.start() - 60) : match.start()]
        subject = context or (
            "Резервуар"
            if re.search(r"резервуар\w*\s*$", prefix, re.I)
            else match["name"]
        )
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "design_decision",
                "subject_name": subject,
                "identifier": "",
                "property_type": "parameter",
                "property_name": match["name"],
                "value_raw": match["value"],
                "unit_raw": match["unit"],
                "evidence_text": match[0],
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            facts.append(fact)
    return tuple(facts)


def merge_text_engineering_facts(
    facts: tuple[DocumentFact, ...], text: str, page: int
) -> tuple[DocumentFact, ...]:
    """Дополняет факты проверенными подписями, сохраняя весь первоначальный результат VLM."""
    facts = tuple(
        replace(fact, source_pages=fact.source_pages or (page,)) for fact in facts
    )
    additions = tuple(
        replace(fact, source_pages=(page,))
        for fact in (
            *explicit_system_purposes(text),
            *explicit_design_parameters(text),
            *explicit_flow_rows(text, page),
        )
    )
    return (
        *facts,
        *(
            fact
            for fact in additions
            if not any(
                old.identifier == fact.identifier
                and old.property_name == fact.property_name
                and old.value_raw == fact.value_raw
                and old.evidence_text == fact.evidence_text
                for old in facts
            )
        ),
    )


def explicit_flow_rows(text: str, page: int) -> tuple[DocumentFact, ...]:
    """Читает однозначные тройки расходов из текста таблицы без угадывания пустых столбцов.

    Часовой и секундный расчётные расходы сохраняются разными свойствами: они могут
    описываться разными расчётными режимами и не обязаны быть простым пересчётом.
    """
    if not re.search(r"м[³3]/(?:ч|час)", text, re.I) or "л/с" not in text:
        return ()
    number = r"\d+[.,]\d+"
    triple = re.compile(
        rf"(?P<day>{number})\s+(?P<hour>{number})\s+(?P<second>{number})(?!\d)"
    )
    result = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = triple.search(line)
        if match is None:
            continue
        label = line[: match.start()].strip()
        evidence = line.strip()
        if re.search(r"После ВПУ|При наличии|СанПиН|^\d", label, re.I):
            label = lines[index - 1].strip() if index else ""
            evidence = label + "\n" + evidence
        if not re.fullmatch(r"[А-Яа-я][А-Яа-я()\-\s]{4,80}", label):
            continue
        if re.search(r"итого|всего|наименование", label, re.I):
            continue
        # Продолжение названия строки не меняет идентичность её числовых столбцов.
        subject = re.sub(r"\s+", " ", label).casefold()
        subject = re.sub(r"\s+контур[а-я]*$", "", subject)
        table = f"p{page}-flow-row-{index}"
        for column, unit, prop in (
            ("hour", "м³/ч", "Часовой расход воды"),
            ("second", "л/с", "Секундный расход воды"),
        ):
            fact = document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "flow_consumer",
                    "identifier": subject,
                    "subject_name": label,
                    "property_type": "parameter",
                    "property_name": prop,
                    "value_raw": match[column],
                    "unit_raw": unit,
                    "table_id": table,
                    "row_label": subject,
                    "column_label": unit,
                    "evidence_text": evidence,
                    "extraction_method": "pdf_text",
                    "column_binding": "pdf_text_header",
                    "source_pages": [page],
                }
            )
            if fact is not None:
                result.append(fact)
    return tuple(result)
