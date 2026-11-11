# services/analysis-service/src/pdrd_analysis_service/application/native_document_context.py

"""Поиск буквальных упоминаний обозначений поверх уже извлечённого PDF.

Это D-контекст для проверки, а не доказательство идентичности физических объектов.
Он дополняет структурированные факты, когда ограниченный ответ Understanding
не включил подпись схемы, строку спецификации или другое представление объекта.
"""

import re

_TAG = re.compile(r"(?<![\w/.])[A-ZА-ЯЁ]{1,3}[-]?\d{1,3}(?:[.]\d{1,3})*(?![\w.])")
_VISUAL_ALIASES = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
_REPRESENTATION = re.compile(
    r"(?:^|\n)\s*(?:принципиальн\w*\s+схема|схема\s+|план\s+|спецификация\s+)", re.I
)


def native_identifier_sources(pages, *, limit: int = 2, text_limit: int = 2400):
    """Находит другие листы по полному обозначению, не по похожей теме или подстроке.

    Смешанные кириллические/латинские глифы используются только для поиска кандидатов
    контекста. Их принадлежность объекту и условия по-прежнему проверяет обычный gate.
    Исходные подписи, страницы и текст источников не переписываются.
    """
    texts, tags = {}, {}
    for page in pages:
        texts[page.page] = "\n".join(
            re.sub(r"[ \t]+", " ", line).strip()
            for line in page.text.splitlines()
            if line.strip()
        )
        tags[page.page] = {
            match[0].translate(_VISUAL_ALIASES): match[0]
            for match in _TAG.finditer(texts[page.page])
            if not re.match(r"(?:ИОС|ТБЭ)\d", match[0])
        }
    result = {}
    for page in pages:
        ranked = []
        for other in pages:
            if other.page == page.page:
                continue
            shared = tags[page.page].keys() & tags[other.page].keys()
            if not shared:
                continue
            text = texts[other.page]
            representation = bool(_REPRESENTATION.search(text))
            rank = (
                -int(representation),
                -len(shared),
                abs(page.page - other.page),
                other.page,
            )
            ranked.append((rank, other.page, shared))
        sources = []
        for _, number, shared in sorted(ranked)[: max(0, limit)]:
            text = texts[number]
            if len(text) > text_limit:
                positions = [
                    match.start()
                    for match in _TAG.finditer(text)
                    if match[0].translate(_VISUAL_ALIASES) in shared
                ]
                start = max(0, min(positions) - text_limit // 2)
                text = text[start : start + text_limit]
            sources.append(
                {
                    "source_id": f"D-p{number:04d}-native-identifiers",
                    "page": number,
                    "match_type": "native_identifier_context",
                    "score": 1.0,
                    "fact_id": None,
                    "subject": ", ".join(tags[number][key] for key in sorted(shared)),
                    "property": "Упоминания обозначений в исходном PDF",
                    "scope": "",
                    "text": text,
                    "evidence_text": text,
                    "visual_regions": [],
                }
            )
        result[page.page] = tuple(sources)
    return result
