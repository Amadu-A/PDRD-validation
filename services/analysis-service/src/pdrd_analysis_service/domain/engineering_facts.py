# services/analysis-service/src/pdrd_analysis_service/domain/engineering_facts.py

"""Дословные инженерные отношения из текста без нормативных догадок и эталонных ответов."""

import re
from collections import Counter
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


def counted_storage_components(text: str) -> tuple[dict, ...]:
    """Читает количество и номинальный объём отдельных классов ёмкостей.

    Состав не означает полезный запас и не создаёт вымышленных идентификаторов
    резервуаров. Каждая запись сохраняет дословный фрагмент для проверки.
    """
    counts = {"один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4}
    pattern = re.compile(
        r"(?P<count>\d+|один|одна|два|две|три|четыре)\s+"
        r"(?:резервуар\w*|емкост\w*)\s+объ[её]мом\s+(?P<volume>\d+(?:[.,]\d+)?)\s*м[³3](?!\w)",
        re.I,
    )
    content = re.sub(r"\s+", " ", text)
    components = tuple(
        {
            "count": counts[match["count"].casefold()]
            if not match["count"].isdigit()
            else int(match["count"]),
            "volume_raw": match["volume"],
            "unit": "м³",
            "evidence_text": match[0],
        }
        for match in pattern.finditer(content)
    )
    if components:
        return components
    listed = re.search(
        r"резервуар\w*[^.!?]{0,90}?объ[её]мом\s+(?P<values>\d+(?:[.,]\d+)?\s*м[³3](?:\s*(?:,|и)\s*\d+(?:[.,]\d+)?\s*м[³3])+)",
        content,
        re.I,
    )
    if listed is None:
        return ()
    volumes = re.findall(r"(\d+(?:[.,]\d+)?)\s*м[³3]", listed["values"])
    return tuple(
        {"count": count, "volume_raw": volume, "unit": "м³", "evidence_text": listed[0]}
        for volume, count in Counter(volumes).items()
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
        fire_condition = re.search(r"\b(?:наружн|внутренн)\w+", match["context"], re.I)
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
                "subject_name": "Наружное пожаротушение"
                if re.search(r"наружн\w*.*пожаротушени", context, re.I)
                else subject,
                "identifier": "пожаротушение"
                if re.search(r"пожаротушени", context, re.I)
                else "",
                "property_type": "design_fire_flow"
                if re.search(r"пожаротушени", context, re.I)
                else "parameter",
                "property_name": match["name"],
                "relation": "accepted_fire_demand"
                if re.search(r"пожаротушени", context, re.I)
                else "",
                "scope_condition": fire_condition[0]
                if fire_condition and re.search(r"пожаротушени", context, re.I)
                else "",
                "value_raw": match["value"],
                "unit_raw": match["unit"],
                "evidence_text": match[0],
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            facts.append(fact)
    return tuple(facts)


def explicit_pipe_choices(text: str) -> tuple[DocumentFact, ...]:
    """Читает диаметр явно обозначенного водопровода; границы участка и режим не выдумывает."""
    content = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?P<quote>(?:[Пп]ротивопожарный\s+)?водопровод\s+(?P<tag>[А-ЯA-Z]\d+(?:-\d+)?)\s+"
        r"(?:прокладывается|выполнен)[^.;]{0,160}?[øØ⌀](?P<diameter>\d+(?:[.,]\d+)?)\s*мм[^.;]{0,50})"
    )
    result = []
    for match in pattern.finditer(content):
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": match["tag"],
                "property_type": "outer_diameter",
                "property_name": "Наружный диаметр",
                "value_raw": match["diameter"],
                "unit_raw": "мм",
                "relation": "accepted_pipe_choice",
                "evidence_text": match["quote"],
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            result.append(fact)
    return tuple(result)


def explicit_head_declarations(text: str) -> tuple[DocumentFact, ...]:
    """Связывает соседние объявления напора с одним явно подписанным назначением сети.

    Гарантированный и требуемый напоры остаются разными свойствами. Общий тег
    системы не доказывает совпадение расчётной точки или режима этих величин.
    """
    content = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        rf"(?P<quote>Гарантированный\s+напор\s+в\s+сетях\s+"
        rf"(?P<available_purpose>[^.;()]{{3,85}}?)\s*\((?P<tag>{_TAG})\)\s*[-–]?\s*"
        rf"(?P<available>\d+(?:[.,]\d+)?)\s*м\.?\s+"
        rf"Требуемый\s+напор\s+в\s+сети\s+(?P<required_purpose>[^.;()]{{3,85}}?)\s+"
        rf"составляет\s+(?P<required>\d+(?:[.,]\d+)?)\s*м)(?!\w)",
        re.I,
    )

    def purpose(value):
        """Сопоставляет только явно прочитанные классы назначения, без вывода из номера В1/В2."""
        if re.search(r"хоз(?:яйственно)?[-\s]*питьев", value, re.I):
            return "potable"
        if re.search(r"противопожар", value, re.I):
            return "fire"
        return None

    result = []
    for match in pattern.finditer(content):
        if not purpose(match["available_purpose"]) or purpose(
            match["available_purpose"]
        ) != purpose(match["required_purpose"]):
            continue
        for role, label in (
            ("available", "Гарантированный напор"),
            ("required", "Требуемый напор"),
        ):
            fact = document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "system",
                    "identifier": match["tag"],
                    "property_type": "parameter",
                    "property_name": label,
                    "relation": "accepted_head_choice"
                    if role == "required"
                    else "declared_available_head",
                    "value_role": role + "_head",
                    "value_raw": match[role],
                    "unit_raw": "м",
                    "scope_system": match["tag"],
                    "evidence_text": match["quote"],
                    "extraction_method": "pdf_text",
                }
            )
            if fact is not None:
                result.append(fact)
    return tuple(result)


def explicit_rain_flow_choices(text: str) -> tuple[DocumentFact, ...]:
    """Читает принятый сток явно названной территории, отдельно от мощности ЛОС.

    Фраза не устанавливает площадь, интенсивность дождя и режим очистки. Эти
    исходные данные нельзя вывести из соседнего числа производительности ЛОС.
    """
    content = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?P<quote>Расч[её]тный\s+расход\s+дождевых\s+вод\s+с\s+"
        r"(?P<catchment>территории\s+[а-яё\s-]{3,70}?)\s+составляет\s+"
        r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:инв\.?\s+)?(?P<unit>л/с|м[³3]/ч))(?!\w)",
        re.I,
    )
    result = []
    for match in pattern.finditer(content):
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "catchment",
                "identifier": match["catchment"],
                "property_type": "design_flow",
                "property_name": "Расчётный расход дождевых вод",
                "relation": "accepted_rain_flow_choice",
                "scope_location": match["catchment"],
                "value_raw": match["value"],
                "unit_raw": match["unit"],
                "evidence_text": match["quote"],
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            result.append(fact)
    return tuple(result)


def explicit_network_lengths(text: str) -> tuple[DocumentFact, ...]:
    """Читает общую протяжённость подписанной сети, не принимая длину участка L за итог."""
    content = re.sub(r"\s+", " ", text)
    patterns = (
        rf"(?:протяж[её]нность|(?:общая|полная|суммарная)\s+длина)\s+(?:проектируем\w*\s+)?(?:сети|системы|трубопровода)\s+(?:(?:водоснабжения|водоотведения|канализации|теплоснабжения|электроснабжения)\s+)?(?P<tag>{_TAG})\b[^;]{{0,90}}?(?P<value>\d+(?:[.,]\d+)?)\s*м(?![\w³/])",
        rf"(?:сеть|система|трубопровод)\s+(?P<tag>{_TAG})\b[^;]{{0,50}}?(?:общая|полная|суммарная)\s+(?:длина|протяж[её]нность)[^;]{{0,30}}?(?P<value>\d+(?:[.,]\d+)?)\s*м(?![\w³/])",
    )
    result = []
    for pattern in patterns:
        for match in re.finditer(pattern, content, re.I):
            if re.match(r"(?:ду|dn)\d", match["tag"], re.I):
                continue
            fact = document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "system",
                    "subject_name": "Система " + match["tag"],
                    "identifier": match["tag"].upper(),
                    "property_type": "parameter",
                    "property_name": "Длина",
                    "value_raw": match["value"],
                    "unit_raw": "м",
                    "value_role": "network_total_length",
                    "evidence_text": match[0],
                    "extraction_method": "pdf_text",
                }
            )
            if fact is not None:
                result.append(fact)
    return tuple(result)


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
            *explicit_head_declarations(text),
            *explicit_rain_flow_choices(text),
            *explicit_flow_rows(text, page),
            *explicit_pipe_choices(text),
            *explicit_network_lengths(text),
            *explicit_protection_choices(text),
        )
    )
    return (
        *facts,
        *(fact for fact in additions if fact not in facts),
    )


def explicit_protection_choices(text: str) -> tuple[DocumentFact, ...]:
    """Читает явно принятый состав запаса и защищаемые объекты, не вычисляя полезный объём."""
    content = re.sub(r"\s+", " ", text)
    result = []
    storage = re.compile(
        r"(?P<quote>(?:Проектом\s+)?предусматривается\s+установка\s+[^.!?]{0,50}?"
        r"(?P<object>резервуар\w*\s+противопожарного\s+запаса\s+воды),?\s+объ[её]мом\s+"
        r"(?P<values>\d+(?:[.,]\d+)?\s*м[³3](?:\s*[,и]\s*\d+(?:[.,]\d+)?\s*м[³3])+))",
        re.I,
    )
    for match in storage.finditer(content):
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "storage_group",
                "identifier": match["object"],
                "property_type": "fire_reserve_choice",
                "property_name": "Состав противопожарного запаса",
                "relation": "accepted_storage_choice",
                "value_raw": match["values"],
                "evidence_text": match["quote"],
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            result.append(fact)
    # Перечень «два резервуара ... и один ...» задаёт тот же вид принятого
    # решения, что перечисление объёмов. Читаем реальную запись, не превращая
    # геометрический состав в полезный противопожарный запас.
    for sentence in re.split(r"(?<=[.!?])\s+(?=[А-ЯЁ])", content):
        if not re.search(r"противопожар|пожаротушен", sentence, re.I):
            continue
        group = re.search(
            r"резервуар\w*\s+(?:противопожарного\s+)?запаса\s+воды", sentence, re.I
        )
        components = counted_storage_components(sentence)
        if (
            group is None
            or not components
            or any(
                fact.relation == "accepted_storage_choice"
                and fact.evidence_text in sentence
                for fact in result
            )
        ):
            continue
        first, last = components[0]["evidence_text"], components[-1]["evidence_text"]
        start, end = sentence.find(first), sentence.rfind(last)
        if start < 0 or end < start:
            continue
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "storage_group",
                "identifier": group[0],
                "property_type": "fire_reserve_choice",
                "property_name": "Состав противопожарного запаса",
                "relation": "accepted_storage_choice",
                "value_raw": sentence[start : end + len(last)],
                "evidence_text": sentence,
                "extraction_method": "pdf_text",
            }
        )
        if fact is not None:
            result.append(fact)
    protected = re.compile(
        r"(?P<quote>(?:Для\s+целей\s+)?(?:наружного\s+и\s+внутреннего|наружного|внутреннего)\s+"
        r"пожаротушения\s+(?P<objects>[^.;]{3,140}?)\s+предусмотрен\w*\s+"
        r"(?P<solution>[^.;]{3,900}))",
        re.I,
    )
    for match in protected.finditer(content):
        for subject in re.split(r"\s+и\s+", match["objects"]):
            if not re.match(r"(?:здани|склад|сооружени|помещени)", subject, re.I):
                continue
            fact = document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "protected_object",
                    "identifier": subject,
                    "property_type": "fire_protection_solution",
                    "property_name": "Решение пожаротушения",
                    "relation": "accepted_protection_solution",
                    "value_raw": match["solution"],
                    "evidence_text": match["quote"],
                    "extraction_method": "pdf_text",
                }
            )
            if fact is not None:
                result.append(fact)
    return tuple(result)


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
