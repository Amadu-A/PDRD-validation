# services/analysis-service/src/pdrd_analysis_service/domain/inventory_description.py

"""Вопрос о составе по явным подписям без вывода количества из грамматического числа."""

import re

from pdrd_analysis_service.domain.document_context import (
    document_fact_from_mapping,
    normalize_label,
)

_DESCRIPTION = re.compile(
    r"(?P<quote>(?:противопожарн\w*\s+)?водопровод\w*\s+"
    r"(?P<system>[ВB]\d{1,3})\s+с\s+пожарным\s+гидрантом)",
    re.I,
)
_POSITION = re.compile(r"(?<!\w)ПГ[-–]?\s*\d{1,3}(?!\w)", re.I)


def inventory_description_facts(pages):
    """Сохраняет буквальное описание и метки водоснабжения, не назначая меткам тип оборудования."""
    result = {page.page: [] for page in pages}
    for page in pages:
        text = page.text
        labels = {
            normalize_label(m[0]).replace("-", "").replace("–", "").replace(" ", "")
            for m in _POSITION.finditer(text)
        }
        if len(labels) < 2:
            for match in _DESCRIPTION.finditer(text):
                result[page.page].append(
                    _fact(
                        page.page,
                        "inventory_description",
                        match["system"],
                        match["quote"],
                        match["quote"],
                    )
                )
        if not re.search(r"водоснабжен|водопровод|пожаротушен", text, re.I):
            continue
        for match in _POSITION.finditer(text):
            value = match[0]
            result[page.page].append(
                _fact(page.page, "inventory_position", value, value, value)
            )
    return {page: tuple(facts) for page, facts in result.items()}


def _fact(page, relation, identifier, value, quote):
    """Не включает номер страницы в проектное обозначение или количество элементов."""
    return document_fact_from_mapping(
        {
            "kind": "relationship",
            "subject_type": "system"
            if relation == "inventory_description"
            else "drawing_position",
            "identifier": identifier,
            "property_type": relation,
            "property_name": "Описание состава"
            if relation == "inventory_description"
            else "Позиционная подпись",
            "relation": relation,
            "value_raw": value,
            "evidence_text": quote,
            "source_pages": [page],
            "extraction_method": "pdf_text",
        }
    )


def inventory_description_checks(observations):
    """Проверяет наличие описания и нескольких явных меток; принадлежность остаётся вопросом."""
    from pdrd_analysis_service.domain.engineering_checks import EngineeringCheck

    markers = tuple(
        item
        for item in observations
        if item.fact.relation == "inventory_position"
        and _POSITION.fullmatch(item.fact.value_raw)
    )
    unique = {
        normalize_label(item.fact.value_raw)
        .replace("-", "")
        .replace("–", "")
        .replace(" ", "")
        for item in markers
    }
    if len(unique) < 2:
        return ()
    result = []
    for item in observations:
        fact = item.fact
        match = _DESCRIPTION.fullmatch(fact.value_raw)
        if (
            fact.relation != "inventory_description"
            or match is None
            or normalize_label(match["system"]) != normalize_label(fact.identifier)
        ):
            continue
        result.append(
            EngineeringCheck(
                "inventory_description",
                "needs_review",
                "system:" + normalize_label(fact.identifier),
                "inventory_description",
                (item, *markers),
                "inventory_description_requires_mapping",
                {"position_labels": sorted({m.fact.value_raw for m in markers})},
            )
        )
    return tuple(result)


def inventory_description_wording(check):
    """Не утверждает, что все метки являются гидрантами или обозначают разные физические объекты."""
    first = check.observations[0].fact
    labels = ", ".join(check.calculation["position_labels"])
    return (
        f"Для {first.identifier} дано общее описание «{first.value_raw}». "
        f"На других листах прочитаны позиционные подписи {labels}. "
        "Уточнить принадлежность этих позиций системе, количество, назначение и размещение гидрантов "
        "и колодцев; согласовать текст, план, схему и спецификацию.",
        "Указать состав по позиционным обозначениям, отдельно для существующих и проектируемых элементов; "
        "различить гидранты, колодцы и устройства забора воды.",
    )
