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
    r"(?P<name>расч[её]тн\w*\s+расход|требуем\w*\s+напор|потер\w*\s+напора|"
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
    additions = tuple(
        replace(fact, source_pages=(page,))
        for fact in (*explicit_system_purposes(text), *explicit_design_parameters(text))
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
