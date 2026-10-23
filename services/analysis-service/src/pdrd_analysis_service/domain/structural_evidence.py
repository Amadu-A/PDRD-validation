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
