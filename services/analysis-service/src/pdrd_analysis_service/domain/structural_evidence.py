# services/analysis-service/src/pdrd_analysis_service/domain/structural_evidence.py

"""Контрдоказательства структурных претензий по полностью переданному тексту страниц."""

import re


def structural_counterevidence(
    comment: str, evidence: str, page_texts: dict[int, str]
) -> str | None:
    """Не выводит отсутствие из неполного списка фактов и различает уровни шифра."""
    text = " ".join(page_texts.values())
    if re.search(r"(?:номер|обозначени)\w*\s+том|содержани\w*\s+том", comment, re.I):
        volumes = set(re.findall(r"\bтом[а]?\s+(\d+(?:\.\d+)+)", text, re.I))
        claimed = set(re.findall(r"(?<![\w.])\d+(?:\.\d+){2,}(?![\w.])", comment))
        if len(volumes) == 1 and len(claimed) > 1 and volumes <= claimed:
            return "document_code_is_not_volume_number"
    if re.search(
        r"(?:физическ\w*|pdf)[^.!?]{0,35}(?:страниц|номер)|номер[^.!?]{0,35}страниц\w*\s+pdf",
        comment + " " + evidence,
        re.I,
    ) and re.search(
        r"(?:номер\w*\s+лист|лист\s*\d|основн\w*\s+надпис)",
        comment + " " + evidence,
        re.I,
    ):
        return "pdf_page_is_not_printed_sheet_number"
    codes = set(re.findall(r"\bИОС\s*\d+(?:\.\d+)*", comment + " " + evidence, re.I))
    codes = {re.sub(r"\s+", "", code).casefold() for code in codes}
    if len(codes) == 2 and re.search(
        r"различ|разн\w*|расхожд|несоответств|противореч|ошиб|не\s+совпад|не\s+согласован",
        comment,
        re.I,
    ):
        first, second = sorted(codes, key=len)
        if second.startswith(first + ".") and all(
            code in re.sub(r"\s+", "", text).casefold() for code in codes
        ):
            return "section_and_subsection_hierarchy"
    missing = re.search(
        r"(?:отсутств\w*|пропущ\w*|не\s+указан\w*)\s+(?:пункт|подпункт)\s*[«\"']?([а-я])(?:[»\"')\s]|$)",
        comment,
        re.I,
    )
    if missing is None:
        missing = re.search(
            r"(?:пункт|подпункт)\s*[«\"']?([а-я])[»\"')\s]+(?:отсутств\w*|пропущ\w*|не\s+указан\w*)",
            comment,
            re.I,
        )
    if missing and re.search(r"оглавлен|содержани", comment, re.I):
        # Заголовок в теле не доказывает его присутствие именно в оглавлении.
        text = " ".join(
            part
            for part in page_texts.values()
            if re.search(r"оглавление|содержание", part, re.I)
            or re.search(
                r"(?<!\w)" + re.escape(missing[1]) + r"\)[^)]{0,400}\.{3,}", part, re.I
            )
        )
    if missing and re.search(
        r"(?<!\w)" + re.escape(missing[1]) + r"\)\s+\S", text, re.I
    ):
        return "claimed_missing_section_present_in_source"
    if re.search(
        r"(?:отсутств\w*|пропущ\w*|пропуск)\s+(?:(?:номер\w*\s+)?раздел\w*|пункт\w*)\s*[«\"']?\d+|раздел\s+\d+\s+(?:отсутств|пропущ)",
        comment,
        re.I,
    ):
        return "section_composition_requirement_not_established"
    return None


def section_continuation(
    comment: str, *, page: int, page_texts: dict[int, str]
) -> dict | None:
    """Устанавливает продолжение конкретного пункта по соседним исходным страницам.

    Возвращает компактные ссылки и цитаты, не копируя раздел в контекст каждого листа.
    Наличие продолжения опровергает обрыв, но не доказывает достаточность мероприятий.
    """
    if not re.search(
        r"обрыв|обрыва|не\s+заверш|неполн|отсутств|не\s+(?:привед|описан|представлен)",
        comment,
        re.I,
    ):
        return None
    target = re.search(
        r"(?:пункт|подпункт|раздел)\w*\s*[«\"']?([а-яё](?:[._]\d+)*|\d+(?:\.\d+)*)\b",
        comment,
        re.I,
    )
    if target is None:
        return None
    key = target[1].casefold().replace("_", ".")
    marker = re.escape(key).replace(r"\.", "[._]")
    heading = re.compile(r"(?m)^\s*" + marker + r"[).]?\s+\S", re.I)
    for start_page in (page - 1, page):
        current = page_texts.get(start_page, "")
        following = page_texts.get(start_page + 1, "")
        # Общие начальные строки листов и отдельные номера не являются продолжением раздела.
        running = {line.strip() for line in current.splitlines()[:24] if line.strip()}
        next_lines = following.splitlines()
        while next_lines and (
            not next_lines[0].strip()
            or next_lines[0].strip() in running
            or next_lines[0].strip().isdigit()
        ):
            next_lines.pop(0)
        following = "\n".join(next_lines)
        found = heading.search(current)
        if found is None or len(following.strip()) < 50:
            continue
        # Новый пункт того же уровня закрывает раздел до границы страницы.
        next_heading = re.compile(
            r"(?m)^\s*(?:[а-яё](?:[._]\d+)*|\d+(?:\.\d+)*)[).]\s+\S", re.I
        )
        if next_heading.search(current, found.end()):
            continue
        first_heading = next_heading.search(following)
        prefix = following[: first_heading.start()] if first_heading else following
        if len(prefix.strip()) < 50:
            continue
        return {
            "section": key,
            "pages": [start_page, start_page + 1],
            "tail": current[-240:],
            "continuation": prefix[:240],
        }
    return None


def consumption_scope(text: str, quote: str) -> str:
    """Привязывает цитату к явно подписанному подразделу, включая два пункта на листе."""
    if not quote.strip():
        return ""
    from pdrd_analysis_service.domain.document_context import normalize_label

    headings = re.compile(
        r"(?ms)^\s*[а-яёa-z][).]\s+.*?(?=^\s*[а-яёa-z][).]\s+|\Z)", re.I
    )
    scopes = set()
    for section in headings.finditer(text):
        normalized = normalize_label(section[0])
        if normalize_label(quote) not in normalized:
            continue
        heading = normalized[: normalized.index(normalize_label(quote))]
        if re.search(r"для\s+объектов\s+непроизводственн\w*\s+назначени", heading):
            scopes.add("nonproduction")
        elif re.search(r"для\s+объектов\s+производственн\w*\s+назначени", heading):
            scopes.add("production")
    return next(iter(scopes)) if len(scopes) == 1 else ""
