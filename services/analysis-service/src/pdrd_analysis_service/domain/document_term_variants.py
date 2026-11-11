# services/analysis-service/src/pdrd_analysis_service/domain/document_term_variants.py

"""Буквальные варианты длинных слов внутри одного проекта, без словаря ответов."""

import re
from collections import defaultdict

_WORD = re.compile(r"[А-Яа-яЁё]{8,35}\Z")


def internal_typo_variant(first: str, second: str) -> bool:
    """Допускает внутреннюю перестановку соседних букв при одинаковом окончании.

    Разные падежи, количества, марки и числовые параметры здесь не сравниваются.
    Даже совпадение этого предиката не доказывает, какой вариант правильный.
    """
    first, second = (
        first.casefold().replace("ё", "е"),
        second.casefold().replace("ё", "е"),
    )
    if not _WORD.fullmatch(first) or not _WORD.fullmatch(second) or first == second:
        return False
    prefix = 0
    while prefix < min(len(first), len(second)) and first[prefix] == second[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(first), len(second)) - prefix
        and first[-1 - suffix] == second[-1 - suffix]
    ):
        suffix += 1
    if prefix < 2 or suffix < 3:
        return False
    a, b = first[prefix : len(first) - suffix], second[prefix : len(second) - suffix]
    # Замена/удаление буквы меняет и корректные слова: «провести/привести»,
    # «контрактов/контактов», «принимается/принимаются». Без словаря и контекста
    # такие пары не являются даже надёжным редакционным кандидатом.
    return len(a) == len(b) == 2 and a == b[::-1]


def native_term_variants(pages):
    """Возвращает наблюдения редкого варианта и реально повторённого образца.

    Используются только родные PDF-слова. Частый образец должен присутствовать
    не менее четырёх раз минимум на двух страницах; неоднозначные пары пропускаются.
    Все страницы обоих вариантов сохраняются, название PDF и экспертные JSON не читаются.
    """
    words = defaultdict(list)
    for page, _, native in pages:
        for index, word in enumerate(native):
            raw = str(word.get("text", "")).strip('.,;:!?()«»"')
            if _WORD.fullmatch(raw):
                words[raw.casefold().replace("ё", "е")].append((page, index, raw, word))
    common = defaultdict(list)
    for token, mentions in words.items():
        if len(mentions) >= 4 and len({item[0] for item in mentions}) >= 2:
            common[(token[:2], token[-3:], len(token))].append(token)
    result = []
    for token, mentions in words.items():
        if len(mentions) > 2:
            continue
        matches = [
            other
            for length in (len(token) - 1, len(token), len(token) + 1)
            for other in common.get((token[:2], token[-3:], length), ())
            if internal_typo_variant(token, other)
        ]
        if len(matches) != 1:
            continue
        reference = matches[0]
        # Несколько появлений одного слова на одной странице — один источник;
        # сохраняются все реальные области и страницы, а не только первый образец.
        for relation, items in (
            ("term_variant", mentions),
            ("term_variant_reference", words[reference]),
        ):
            by_page = defaultdict(list)
            for item in items:
                by_page[item[0]].append(item)
            for page, items_on_page in by_page.items():
                regions = [
                    item[3]["bbox"] for item in items_on_page if item[3].get("bbox")
                ]
                result.append(
                    (
                        page,
                        relation,
                        items_on_page[0][2],
                        token + ":" + reference,
                        regions,
                    )
                )
    return result
