# services/analysis-service/src/pdrd_analysis_service/domain/document_quality.py

"""Буквальные реквизиты PDF и предметные вопросы, не требующие новых VLM-вызовов.

Проверки оформления не подменяют нормативный контроль. Наблюдение без достаточного
основания превращается в конкретный вопрос, а не в доказанное нарушение ГОСТ.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from itertools import pairwise

from pdrd_analysis_service.domain.document_context import (
    document_fact_from_mapping,
    normalize_label,
)
from pdrd_analysis_service.domain.document_term_variants import (
    internal_typo_variant,
    native_term_variants,
)

_CODE = re.compile(r"(?<![\w/])\d{2,5}/\d{2,5}[-–][\w.-]{1,65}", re.I)
_TABLE = re.compile(r"^Таблица\s+(\d+(?:\.\d+)*)(?:\.)?(?:\s*[-–—]\s*(.*))?\s*$", re.I)
_SECTION = re.compile(
    r"(?P<project>\d+/\d+[-–][\w.-]*?)-(?P<section>ИОС\d+(?:\.\d+)*|ГОЧС\d*|ТБЭ\d*|ПБ\d*)",
    re.I,
)
_PROJECT_TITLE = re.compile(r"комплекс\s+зданий\s+и\s+сооружений\s+объект[а-яё]*", re.I)
_REPEATED_WORD = re.compile(r"(?<!\w)(не|для|при|по|из|или)\s+\1(?!\w)", re.I)
_SDR = re.compile(
    r"\bSDR\s*(?P<sdr>\d+(?:[.,]\d+)?)(?:\s*[-–]?\s*)(?P<d>\d{2,4})\s*[хx×*]\s*(?P<t>\d+(?:[.,]\d+)?)",
    re.I,
)
_NORM = re.compile(
    r"\b(?:СП|ГОСТ(?:\s+Р)?|СНиП)\s+\d{1,5}(?:[.\-–]\d{1,9})+(?!\w|[.\-–]\d)", re.I
)
_INTERVAL = re.compile(
    r"(?:не\s+(?:реже|позднее)|не\s+менее|один\s+раз|\d+\s+раз[а]?)\s+(?:в\s+)?\d*\s*(?:дн\w*|недел\w*|месяц\w*|год\w*|лет)|ежегодн\w*|ежемесячн\w*|еженедельн\w*",
    re.I,
)
_MAINTENANCE = re.compile(
    r"осмотр|испытан|обследован|ремонт|провер[кя]|обслуживан", re.I
)
_ABBREVIATIONS = re.compile(r"(?<![\w-])[А-ЯЁ]{2,5}(?![\w-])")
_OTHER_SCOPE = re.compile(
    r"внутридомов\w*|жил(?:ого|ых|ой|ым|ыми)\s+дом\w*|квартир\w*|лифт(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|"
    r"железнодорожн\w*|фреон\w*|этиленгликол\w*|конденсатоотводчик\w*|продувк\w*\s+(?:паропровод\w*|паром)",
    re.I,
)
_COMMON_ABBREVIATIONS = frozenset(
    [
        "РФ",
        "СССР",
        "ООО",
        "ОАО",
        "АО",
        "ПАО",
        "ГОСТ",
        "СП",
        "СНиП",
        "ТУ",
        "РД",
        "ФЗ",
        "ПУЭ",
        "ПТЭЭП",
        "ПТЭ",
        "МЧС",
        "МВД",
        "РЖД",
        "ГУ",
        "ГПИСС",
        "ИОС",
        "ПБ",
        "ГОЧС",
        "ТБЭ",
        "ПЗ",
        "ТЧ",
        "ГЧ",
        "СО",
        "ПД",
        "АР",
        "КР",
        "КЖ",
        "КМ",
        "ОВ",
        "ВК",
        "ГВС",
        "ХВС",
        "ИТП",
        "ЦТП",
        "ГРП",
        "ГРУ",
        "ШРП",
        "АГРС",
        "ПЭ",
        "ПНД",
        "ПВХ",
        "ПП",
        "ППУ",
        "ППР",
        "ПДК",
        "ПДУ",
        "ГСОП",
        "КПД",
        "УЗО",
        "АВР",
        "АУПТ",
        "АУПС",
        "АПС",
        "СОУЭ",
        "СКУД",
        "АСКУЭ",
        "АСУ",
        "ТП",
        "СРО",
        "НИИ",
        "ПИР",
        "ТЭЦ",
        "ГЭС",
        "ГРЭС",
        "ТЭС",
        "ТС",
        "ГО",
        "ЕАС",
        "ЕСКД",
        "СПДС",
        "КИП",
        "ОТ",
        "ВОЗ",
        "ТБО",
        "ТКО",
        "НДС",
        "ОКПД",
        "ОКВЭД",
        "ЛОС",
        "СЗЗ",
        "ЗСО",
        "БМК",
        "АИТ",
        "СУГ",
        "РВС",
        "ПЛА",
        "ПМЛА",
        "ГИП",
        "ГАП",
        "НК",
        "РЭ",
        "НТД",
        "СИЗ",
        "СИЗОД",
        "КЧС",
        "РСЧС",
        "ЕДДС",
        "ПАСС",
        "ПАСФ",
        "НАСФ",
        "ОПО",
        "КНС",
        "НС",
        "НПА",
        "ТО",
        "ТР",
        "ИТР",
        "ДЭС",
        "ДГУ",
        "ДТ",
        "АБК",
        "АТХ",
        "ЭОМ",
        "ЭМ",
        "ЭО",
        "ЭС",
        "ГСВ",
        "ГСН",
        "ВРУ",
        "ЩУ",
        "ЩС",
        "УУТЭ",
        "КТП",
        "РЗА",
        "ИБП",
        "АКБ",
        "ПК",
        "ЧС",
        "АХОВ",
        "ПНС",
        "ТН",
        "ВД",
        "ТЭН",
        "ТЭР",
        "ТЭО",
        "ТЭП",
        "ГТУ",
        "ГТД",
        "ПГ",
        "СТО",
        "ПДВ",
        "ПГС",
        "ЖБИ",
        "ЖБК",
        "КС",
        "ГНБ",
        "ПСИ",
        "ПНР",
        "СПЗ",
        "СВТ",
        "СКС",
        "ЛВС",
        "БД",
        "ФИО",
        "ИНН",
        "КПП",
        "ОГРН",
        "ПОС",
        "ПЗУ",
        "ООС",
        "ВПУ",
        "ХПК",
        "БПК",
        "НКПРП",
        "НПО",
        "ИСО",
        "ВСН",
        "ССБТ",
        "ИТОГО",
    ]
)


def _fact(page, relation, value, quote, *, identifier="", regions=(), **values):
    """Записывает исходный фрагмент без потери страницы и области доказательства."""
    return document_fact_from_mapping(
        {
            "kind": "identifier",
            "subject_type": "document",
            "subject_name": identifier or value,
            "identifier": identifier or value,
            "property_type": "document_quality",
            "property_name": relation,
            "relation": relation,
            "value_raw": value,
            "evidence_text": quote,
            "extraction_method": "pdf_text",
            "source_pages": [page],
            "visual_regions": list(regions),
            **values,
        }
    )


def _lines(words):
    """Собирает строки только по родным PDF block/line, не склеивая соседние колонки."""
    groups = defaultdict(list)
    for word in words:
        box = word.get("bbox", {})
        if all(key in box for key in ("x_min", "y_min", "x_max", "y_max")):
            groups[(word.get("block_no"), word.get("line_no"))].append(word)
    result = []
    for key, members in groups.items():
        members.sort(key=lambda item: item.get("word_no", 0))
        result.append(
            (
                " ".join(str(word.get("text", "")) for word in members).strip(),
                {
                    "x_min": int(min(w["bbox"]["x_min"] for w in members)),
                    "y_min": int(min(w["bbox"]["y_min"] for w in members)),
                    "x_max": int(max(w["bbox"]["x_max"] for w in members)),
                    "y_max": int(max(w["bbox"]["y_max"] for w in members)),
                    "confidence": 1.0,
                    "label": "Текст PDF",
                },
                key,
            )
        )
    return sorted(result, key=lambda line: (line[1]["y_min"], line[1]["x_min"]))


def document_quality_facts(pages):
    """Извлекает реквизиты, состав труб и буквальные повторы из доступного документа.

    Вход: (физическая страница, исходный текст, родные PDF-слова). Ни имя файла,
    ни экспертный JSON, ни предполагаемые правильные значения здесь не используются.
    """
    result = {page: [] for page, _, _ in pages}
    full_text = "\n".join(text for _, text, _ in pages)
    definitions = _defined_abbreviations(full_text)
    paragraph_mentions = defaultdict(list)
    code_mentions = []
    anchors = []
    for page, text, words in pages:
        lines = _lines(words)
        if page <= 2:
            anchor = re.search(r"(?:Блочно[-– ]модульная\s+)?котельн\w*", text, re.I)
            if anchor:
                anchors.append((page, anchor[0]))
        for line, box, _ in lines:
            title = _PROJECT_TITLE.search(line)
            project = _CODE.search(line[title.end() :]) if title else None
            if title and project and (page == 1 or box["y_min"] > 700):
                result[page].append(
                    _fact(
                        page,
                        "project_title_reference" if page == 1 else "project_title",
                        title[0],
                        line,
                        identifier=project[0].rstrip("."),
                        regions=(box,),
                    )
                )
            if page > 1 and box["y_max"] > 750 and _CODE.fullmatch(line.strip()):
                for match in _CODE.finditer(line):
                    code_mentions.append((page, match[0].rstrip("."), line, box))
            table = _TABLE.fullmatch(line)
            if table and not table[2]:
                # Номер справа не ограничивает ширину таблицы. Первую строку
                # отличаем от содержательного названия по раздельным колонкам.
                following = [
                    other
                    for other in lines
                    if box["y_max"] - 1 <= other[1]["y_min"] < box["y_max"] + 35
                    and other[1]["x_max"] > 80
                    and other[1]["y_max"] - other[1]["y_min"] <= 32
                ]
                first = min(following, key=lambda item: item[1]["y_min"], default=None)
                headers = []
                if first is not None:
                    first_box = first[1]
                    for other in following:
                        other_box = other[1]
                        overlap = min(first_box["y_max"], other_box["y_max"]) - max(
                            first_box["y_min"], other_box["y_min"]
                        )
                        shorter = min(
                            first_box["y_max"] - first_box["y_min"],
                            other_box["y_max"] - other_box["y_min"],
                        )
                        if overlap >= shorter / 2:
                            headers.append(other)
                intervals = sorted(
                    (other[1]["x_min"], other[1]["x_max"]) for other in headers
                )
                separated = any(
                    second[0] - first[1] >= 75 for first, second in pairwise(intervals)
                )
                header_text = " ".join(other[0] for other in headers)
                named_header = re.search(
                    r"наименовани[ея]|№\s*п|ед\.?\s*изм|показател|температур|периодичност|срок",
                    header_text,
                    re.I,
                )
                quantitative = re.search(
                    r"расход|мощност|объем|объём", header_text, re.I
                ) and re.search(
                    r"(?<!\w)(?:кВт|Вт|Па|л/с|м[³3](?:/ч)?)(?!\w)", header_text, re.I
                )
                if separated and (named_header or quantitative):
                    quote = line
                    result[page].append(
                        _fact(
                            page,
                            "table_caption",
                            quote,
                            quote,
                            regions=(box,),
                            source_item_id=f"pdf-page-{page}:table-caption:{box['y_min']}:{box['x_min']}",
                        )
                    )
        for match in _SDR.finditer(text):
            result[page].append(_fact(page, "pipe_sdr", match[0], match[0]))
        for match in _REPEATED_WORD.finditer(text):
            result[page].append(
                _fact(
                    page,
                    "repeated_word",
                    match[0],
                    match[0],
                    source_item_id=f"pdf-page-{page}:text-offset:{match.start()}",
                )
            )
        for match in _NORM.finditer(text):
            code = match[0]
            tail = re.escape(re.sub(r"\s+", " ", code))
            if (
                re.match(r"СП\s", code, re.I)
                and not re.search(r"(?:[.\-–](?:19|20)\d{2})$", code)
                and not re.search(r"[-–]\d{2}$", code)
                and not re.search(
                    tail + r"[.\-–](?:19|20)\d{2}", re.sub(r"\s+", " ", full_text), re.I
                )
            ):
                result[page].append(_fact(page, "reference_edition", code, code))
        for paragraph_index, paragraph in enumerate(_paragraphs(text), 1):
            normalized = normalize_label(paragraph)
            if (
                len(normalized) >= 160
                and len(normalized.split()) >= 20
                and paragraph.endswith((".", ";"))
                and not _CODE.search(paragraph)
                and not re.search(r"Инв\.|Взам\.|Подп\.|Кол\.уч", paragraph)
            ):
                paragraph_mentions[normalized].append((page, paragraph))
            if (
                len(paragraph) <= 1600
                and _MAINTENANCE.search(paragraph)
                and _INTERVAL.search(paragraph)
                and not _NORM.search(paragraph)
            ):
                result[page].append(
                    _fact(
                        page,
                        "maintenance_interval",
                        paragraph,
                        paragraph,
                        source_item_id=f"pdf-page-{page}:maintenance-paragraph:{paragraph_index}",
                    )
                )
            if (
                _OTHER_SCOPE.search(paragraph)
                and not _NORM.search(paragraph)
                and len(paragraph) <= 1200
            ):
                result[page].append(
                    _fact(page, "instruction_scope", paragraph, paragraph)
                )
        for line in _paragraphs(text):
            line = line.strip()
            for match in _ABBREVIATIONS.finditer(line):
                abbreviation = match[0]
                if (
                    abbreviation not in definitions
                    and abbreviation not in _COMMON_ABBREVIATIONS
                    and _prose_abbreviation(line, match)
                ):
                    result[page].append(_fact(page, "abbreviation", abbreviation, line))
    for mentions in paragraph_mentions.values():
        if len(mentions) < 2:
            continue
        first = mentions[0][1]
        for page, quote in mentions:
            result[page].append(
                _fact(page, "repeated_paragraph", quote, quote, identifier=first)
            )
    _document_codes(result, code_mentions, pages)
    for page, relation, value, identifier, regions in native_term_variants(pages):
        result[page].append(
            _fact(page, relation, value, value, identifier=identifier, regions=regions)
        )
    if anchors:
        page, quote = anchors[0]
        result[page].append(_fact(page, "project_purpose", quote, quote))
    return {page: tuple(_unique(facts)) for page, facts in result.items()}


def _unique(facts):
    """Исключает только одинаковые факты, сохраняя независимые области и страницы."""
    seen = set()
    for fact in facts:
        key = (
            fact.relation,
            fact.value_raw,
            fact.evidence_text,
            repr(fact.visual_regions),
            fact.source_item_id,
        )
        if key not in seen:
            seen.add(key)
            yield fact


def _paragraphs(text):
    """Склеивает строки одного предложения; ограничивает объём исходного фрагмента."""
    return tuple(
        part.strip()
        for part in re.split(r"(?<=[.!?;])\s+(?=[А-ЯЁA-Z0-9])|\n\s*\n", text)
        if part.strip() and len(part.strip()) <= 2000
    )


def _prose_abbreviation(line, match):
    """Не принимает марку изделия, код нормы, единицу или строку спецификации за сокращение."""
    return (
        25 <= len(line) <= 1600
        and not re.search(r"\s{3,}", line)
        and re.search(r"[а-яё]{7,}", line)
        and not re.match(r"[ .-]*\d", line[match.end() :])
        and not re.match(r"[.\-/]+[A-ZА-ЯЁ0-9]", line[match.end() :])
        and re.search(
            r"предусмотрен|представля|провод|выполня|обслужива|проверя|долж|следует|необходим|"
            r"осуществля|запрещ|разреш|допуска|обеспечива|использу|применя|составля|требуется|устанавлива",
            line,
            re.I,
        )
        and not re.search(r"[«\"]|ГОСТ|ТУ |марка|модель|шт\.", line)
        and not re.search(
            r"(?:контроллер[а-я]*|производител[а-я]*|изготовител[а-я]*|на\s+базе)\s*$",
            line[: match.start()],
            re.I,
        )
        and not re.search(r"[А-ЯЁ]{6}", line)
    )


def _defined_abbreviations(text):
    """Учитывает раскрытие в скобках и отдельный перечень сокращений всего проекта."""
    result = set(
        re.findall(
            r"[А-Яа-яЁё][^()\n]{8,150}\(\s*(?:далее(?:\s+по\s+тексту)?\s*[-–—:]\s*)?([А-ЯЁ]{2,5})\s*\)",
            text,
        )
    )
    result.update(
        re.findall(r"(?:^|\n)\s*([А-ЯЁ]{2,5})\s*[-–—:]\s*[А-Яа-яЁё]{3}", text)
    )
    return result


def _document_codes(result, mentions, pages):
    """Сопоставляет суффикс текстовой части с явным шифром, а не с именем PDF."""
    explicit = defaultdict(list)
    cover_codes = []
    manifests = []
    contents_pages = {
        page
        for page, text, _ in pages
        if re.search(r"содержание\s+тома", text[:200], re.I)
    }
    for page, text, words in pages:
        # sort=True иногда склеивает шифр и вертикальное «Взам.инв.».
        # Отдельное PDF-слово сохраняет границы поля без исправления его текста.
        codes = [match[0].rstrip(".") for match in _CODE.finditer(text)]
        codes.extend(
            str(word.get("text", "")).rstrip(".")
            for word in words
            if _CODE.fullmatch(str(word.get("text", "")).rstrip("."))
        )
        for code in dict.fromkeys(codes):
            if code.endswith(("-ТЧ", "-С")):
                explicit[code].append((page, code))
            if page == 1 and _SECTION.fullmatch(code):
                cover_codes.append((page, code))
        if _separate_part_headings(text):
            manifests.append((page, text, set(codes)))
    role_codes = set()
    if len({code for _, code in cover_codes}) == 1:
        base = cover_codes[0][1]
        for page, text, codes in manifests:
            if base + "-С" in codes:
                role_codes.add(base)
                result[page].append(
                    _fact(
                        page,
                        "document_part_role_reference",
                        "Текстовая часть",
                        text,
                        identifier=base,
                    )
                )
    for page, code, quote, box in mentions:
        base = code.removesuffix("-ГЧ").removesuffix("-ТЧ").removesuffix("-СО")
        actual = _SECTION.fullmatch(base)
        if actual and len({value for _, value in cover_codes}) == 1:
            source_page, expected = cover_codes[0]
            reference = _SECTION.fullmatch(expected)
            if (
                reference
                and actual["project"] == reference["project"]
                and actual["section"] != reference["section"]
            ):
                result[page].append(
                    _fact(page, "section_code", base, quote, regions=(box,))
                )
                result[source_page].append(
                    _fact(source_page, "section_code_reference", expected, expected)
                )
        expected = code + ("-С" if page in contents_pages else "-ТЧ")
        if expected in explicit:
            result[page].append(
                _fact(page, "document_part_code", code, quote, regions=(box,))
            )
            for source_page, reference in explicit[expected]:
                result[source_page].append(
                    _fact(source_page, "document_part_reference", reference, reference)
                )
        elif code in role_codes:
            result[page].append(
                _fact(page, "document_part_role", code, quote, regions=(box,))
            )


def _separate_part_headings(text):
    """Отличает отдельные части в составе тома от единого комбинированного документа."""
    return bool(
        re.search(r"обозначение\s+наименование", text, re.I)
        and re.search(r"^\s*Текстовая\s+часть\s*$", text, re.I | re.M)
        and re.search(r"^\s*Графическая\s+часть\s*$", text, re.I | re.M)
    )


def quality_checks(observations):
    """Повторяет предметный предикат по исходной цитате перед созданием вопроса."""
    from pdrd_analysis_service.domain.engineering_checks import EngineeringCheck

    result = []
    groups = defaultdict(list)
    for item in observations:
        fact = item.fact
        if (
            fact.property_type != "document_quality"
            or fact.extraction_method != "pdf_text"
        ):
            continue
        rule = fact.relation
        if rule == "pipe_sdr":
            match = _SDR.fullmatch(fact.value_raw)
            if match is None:
                continue
            sdr, diameter, thickness = (
                Decimal(match[key].replace(",", ".")) for key in ("sdr", "d", "t")
            )
            if min(sdr, diameter, thickness) <= 0 or diameter <= thickness * 2:
                continue
            actual = diameter / thickness
            # Номинальная стенка округляется вверх; 5% оставляют нормальные ряды
            # размеров в покое. Точный ГОСТ-ряд здесь не выдумывается.
            if abs(actual - sdr) / sdr <= Decimal("0.05"):
                continue
            values = {
                "sdr": str(sdr),
                "diameter": str(diameter),
                "thickness": str(thickness),
                "ratio": str(round(actual, 3)),
            }
            reason = "pipe_sdr_dimensions_require_review"
        elif rule == "table_caption" and _TABLE.fullmatch(fact.value_raw):
            values, reason = {}, "table_name_requires_review"
        elif rule == "reference_edition" and _NORM.fullmatch(fact.value_raw):
            values, reason = {}, "reference_edition_requires_review"
        elif rule == "repeated_word" and _REPEATED_WORD.fullmatch(fact.value_raw):
            values, reason = {}, "repeated_function_word"
        elif (
            rule == "maintenance_interval"
            and _INTERVAL.search(fact.value_raw)
            and _MAINTENANCE.search(fact.value_raw)
        ):
            values, reason = {}, "maintenance_basis_requires_review"
        elif (
            rule == "abbreviation"
            and _ABBREVIATIONS.fullmatch(fact.value_raw)
            and fact.value_raw not in _COMMON_ABBREVIATIONS
        ):
            values, reason = {}, "abbreviation_requires_review"
        elif rule == "repeated_paragraph":
            groups[(rule, normalize_label(fact.value_raw))].append(item)
            continue
        elif rule in {"document_part_code", "document_part_reference"}:
            groups[
                (rule, fact.value_raw.removesuffix("-ТЧ").removesuffix("-С"))
            ].append(item)
            continue
        elif rule == "document_part_role_reference":
            if (
                _separate_part_headings(fact.evidence_text)
                and fact.identifier + "-С" in fact.evidence_text
            ):
                groups[(rule, fact.identifier)].append(item)
            continue
        elif rule == "document_part_role" and _CODE.fullmatch(fact.value_raw):
            groups[(rule, fact.value_raw)].append(item)
            continue
        elif rule in {"project_purpose", "instruction_scope"}:
            groups[(rule, "scope")].append(item)
            continue
        elif rule in {"section_code", "section_code_reference"}:
            parsed = _SECTION.fullmatch(fact.value_raw)
            if parsed:
                groups[(rule, parsed["project"])].append(item)
            continue
        elif rule in {"project_title", "project_title_reference"}:
            if _PROJECT_TITLE.fullmatch(fact.value_raw):
                groups[(rule, fact.identifier)].append(item)
            continue
        elif rule in {"term_variant", "term_variant_reference"}:
            groups[(rule, fact.identifier)].append(item)
            continue
        else:
            continue
        result.append(
            EngineeringCheck(
                "document_quality",
                "needs_review",
                fact.identifier,
                rule,
                (item,),
                reason,
                values,
            )
        )
    for (rule, _), members in groups.items():
        if (
            rule == "repeated_paragraph"
            and len(members) > 1
            and len({item.page for item in members}) > 1
        ):
            result.append(
                EngineeringCheck(
                    "document_quality",
                    "needs_review",
                    members[0].fact.identifier,
                    rule,
                    tuple(members),
                    "paragraph_repeated",
                    {},
                )
            )
    for (rule, code), members in groups.items():
        if rule != "document_part_code":
            continue
        references = groups.get(("document_part_reference", code), ())
        if references:
            for member in members:
                contents = any(
                    ref.page == member.page and ref.fact.value_raw.endswith("-С")
                    for ref in references
                )
                ref = next(
                    (
                        ref
                        for ref in references
                        if ref.fact.value_raw.endswith("-С" if contents else "-ТЧ")
                    ),
                    None,
                )
                if ref is None:
                    continue
                result.append(
                    EngineeringCheck(
                        "document_quality",
                        "needs_review",
                        code,
                        rule,
                        (member, ref),
                        "part_code_requires_review",
                        {"expected": ref.fact.value_raw},
                    )
                )
    for (rule, project), members in groups.items():
        if rule != "section_code":
            continue
        refs = groups.get(("section_code_reference", project), ())
        for item in members:
            for ref in refs[:1]:
                if item.fact.value_raw != ref.fact.value_raw:
                    result.append(
                        EngineeringCheck(
                            "document_quality",
                            "needs_review",
                            item.fact.identifier,
                            "section_code",
                            (item, ref),
                            "section_code_differs_from_cover",
                            {},
                        )
                    )
    anchors = groups.get(("project_purpose", "scope"), ())
    for (rule, identifier), members in groups.items():
        if rule != "term_variant":
            continue
        references = groups.get(("term_variant_reference", identifier), ())
        if not references or len({item.page for item in references}) < 2:
            continue
        if not all(
            internal_typo_variant(item.fact.value_raw, ref.fact.value_raw)
            for item in members
            for ref in references
        ):
            continue
        result.append(
            EngineeringCheck(
                "document_quality",
                "needs_review",
                identifier,
                "term_variant",
                (*members, *references),
                "literal_term_variants_require_review",
                {},
            )
        )
    for (rule, project), members in groups.items():
        if rule != "project_title":
            continue
        references = groups.get(("project_title_reference", project), ())
        if len({normalize_label(ref.fact.value_raw) for ref in references}) != 1:
            continue
        ref = references[0]
        for item in members:
            if normalize_label(item.fact.value_raw) != normalize_label(
                ref.fact.value_raw
            ):
                result.append(
                    EngineeringCheck(
                        "document_quality",
                        "needs_review",
                        project,
                        "project_title",
                        (item, ref),
                        "project_title_differs_from_cover",
                        {},
                    )
                )
    for (rule, code), members in groups.items():
        if rule != "document_part_role":
            continue
        references = groups.get(("document_part_role_reference", code), ())
        if references:
            for member in members:
                result.append(
                    EngineeringCheck(
                        "document_quality",
                        "needs_review",
                        code,
                        "document_part_role",
                        (member, references[0]),
                        "document_part_role_requires_review",
                        {},
                    )
                )
    if anchors:
        for item in groups.get(("instruction_scope", "scope"), ()):
            if _OTHER_SCOPE.search(item.fact.value_raw):
                result.append(
                    EngineeringCheck(
                        "document_quality",
                        "needs_review",
                        item.fact.identifier,
                        "instruction_scope",
                        (item, anchors[0]),
                        "instruction_scope_requires_review",
                        {},
                    )
                )
    return tuple(result)


def quality_wording(check):
    """Формулирует ограниченный вывод без заявления о нарушении непереданной нормы."""
    fact = check.observations[0].fact
    value = fact.value_raw
    rule = check.property_key
    if rule == "repeated_word":
        return (
            f"В записи «{value}» дважды подряд повторено одно служебное слово. Проверить редакционную ошибку.",
            "Убрать случайный повтор, сохранив исходный технический смысл предложения.",
        )
    if rule == "section_code":
        return (
            f"В основной надписи указан шифр «{value}», а на обложке этого комплекта — "
            f"«{check.observations[1].fact.value_raw}». Проверить принадлежность листа и номер раздела.",
            "Уточнить шифр основной надписи и согласовать его с обозначением проверяемого комплекта.",
        )
    if rule == "project_title":
        return (
            f"Наименование объекта «{value}» отличается от записи на обложке "
            f"«{check.observations[1].fact.value_raw}» при одном шифре объекта "
            f"«{fact.identifier}». Согласовать наименование и его грамматическую форму.",
            "Проверить наименование в основных надписях и реквизитах комплекта по обложке и исходным данным.",
        )
    if rule == "term_variant":
        reference = next(
            item.fact.value_raw
            for item in check.observations
            if item.fact.relation == "term_variant_reference"
        )
        return (
            f"Проверить возможную опечатку «{value}»: в этом же проекте используется вариант «{reference}». "
            "Согласовать написание с учётом смысла предложения.",
            "Сверить исходное слово и повторяющийся вариант; не менять технический смысл и обозначения изделий.",
        )
    if rule == "pipe_sdr":
        values = check.calculation
        return (
            f"В обозначении трубы «{value}» указаны SDR {values['sdr']}, "
            f"наружный диаметр {values['diameter']} мм и стенка {values['thickness']} мм. "
            f"Отношение диаметра к стенке равно {values['ratio']}; "
            "оно не согласуется с указанным SDR с учётом округления.",
            "Сверить размер трубы и SDR по применимому стандарту и паспорту; "
            "согласовать текст, схемы и спецификацию.",
        )
    if rule == "table_caption":
        return (
            f"У таблицы «{value}» прочитан номер непосредственно над шапкой колонок. "
            "Уточнить наименование таблицы и единообразное оформление её заголовка.",
            "Согласовать номер и содержательное наименование в подписи таблицы.",
        )
    if rule == "reference_edition":
        return (
            f"В ссылке «{value}» не установлена однозначная редакция документа. "
            "Уточнить полный шифр и год применённой редакции.",
            "Согласовать ссылку с перечнем нормативных документов проекта и проверить применимость редакции.",
        )
    if rule == "abbreviation":
        return (
            f"Уточнить расшифровку сокращения «{value}» в записи «{fact.evidence_text}».",
            "Раскрыть сокращение при первом упоминании либо добавить его в перечень сокращений проекта.",
        )
    if rule == "maintenance_interval":
        excerpt = _excerpt(value, _INTERVAL)
        return (
            f"Уточнить основание принятой периодичности или срока обслуживания: «{excerpt}».",
            "Указать применимый пункт эксплуатационной документации или нормы "
            "для конкретного оборудования и условий.",
        )
    if rule == "instruction_scope":
        purpose = check.observations[1].fact.value_raw
        excerpt = _excerpt(value, _OTHER_SCOPE)
        return (
            f"Для объекта «{purpose}» уточнить применимость указания «{excerpt}». "
            "Фрагмент относится к иному виду объекта или специальной системе; "
            "его связь с принятыми проектными решениями требует пояснения.",
            "Указать конкретное здание, систему или оборудование, к которым относится указание, "
            "либо исключить неприменимый шаблонный текст.",
        )
    if rule == "document_part_code":
        part = (
            "содержания тома"
            if check.calculation["expected"].endswith("-С")
            else "текстовой части"
        )
        return (
            f"В основной надписи указан шифр «{value}». Проверить обозначение {part} "
            f"и его согласованность с «{check.calculation['expected']}», приведённым в комплекте.",
            "Согласовать реквизиты документа с составом комплекта; "
            "указать обозначение, принятое для этого документа.",
        )
    if rule == "document_part_role":
        return (
            f"В основной надписи указан общий шифр «{value}», "
            "при этом в содержании тома выделены отдельные текстовая и графическая части. "
            "Уточнить обозначение этой части (ТЧ/ГЧ) и его согласованность с составом тома.",
            "Согласовать обозначения текстовой и графической частей в содержании и основных надписях; "
            "проверить суффикс, принятый для конкретного документа.",
        )
    if rule == "repeated_paragraph":
        pages = ", ".join(map(str, sorted({item.page for item in check.observations})))
        return (
            f"На страницах {pages} повторяется один и тот же фрагмент: «{_excerpt(value)}». "
            "Проверить необходимость повторения и соответствие разделам.",
            "Сохранить необходимое содержание и заменить избыточный повтор точной внутренней ссылкой, "
            "если фрагменты относятся к одному вопросу.",
        )
    raise ValueError(f"Неизвестная проверка реквизита: {rule}")


def _excerpt(text, anchor=None):
    """Сокращает только подпись карточки; полная цитата остаётся в evidence и источниках."""
    if len(text) <= 240:
        return text
    match = anchor.search(text) if anchor is not None else None
    start = max(0, match.start() - 100) if match else 0
    return (
        ("…" if start else "")
        + text[start : start + 240].strip()
        + ("…" if start + 240 < len(text) else "")
    )
