# services/equipment-search-service/src/pdrd_equipment_search_service/domain/search_candidates.py

"""Консервативный отсев явно чужих моделей до расходования попытки загрузки."""

import re
from urllib.parse import unquote, urlsplit

from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    SearchHit,
    equipment_token,
)


def clearly_other_model(identity: EquipmentIdentity, hit: SearchHit) -> bool:
    """Отсекает только явную другую модель, никогда не подтверждает применимость.

    Каталоги и неоднозначные заголовки сохраняются. Параметры URL не читаются:
    они могут содержать токены доступа и не являются названием документа.
    """
    try:
        path = unquote(urlsplit(hit.url).path)
    except ValueError:
        return False
    title = hit.title[:1000]
    path = path[-1000:]
    text = (title + " " + path).casefold().translate(str.maketrans("–—−_", "----"))
    model = identity.model.casefold().translate(str.maketrans("–—−", "---"))
    # Гибкая запись модели не даёт Сигнал-100 подтвердить Сигнал-10.
    parts = [re.escape(part) for part in re.split(r"[\s-]+", model) if part]
    if not parts:
        return False
    expected = r"[\s-]*".join(parts)
    if re.search(r"(?<![\w])" + expected + r"(?![\w])", text):
        return False
    if re.search(r"\b(?:каталог|catalog|catalogue|серия|series|линейка)\b", text):
        return False
    family = re.fullmatch(r"(.+?)[\s-]*(\d[\w.-]*)", model)
    if family is not None:
        prefix = r"[\s-]*".join(
            re.escape(part) for part in re.split(r"[\s-]+", family[1]) if part
        )
        alternatives = set(
            re.findall(r"(?<![\w])" + prefix + r"[\s-]*(\d[\w-]*)(?![\w])", text)
        )
        if len(alternatives) == 1:
            alternative = next(iter(alternatives))
            return equipment_token(alternative) != equipment_token(family[2])
    labelled = re.findall(
        r"\b(?:модель|model)\s*[:=]\s*([\w]+(?:[ -][\w]+){0,2})", title.casefold()
    )
    return len(labelled) == 1 and equipment_token(labelled[0]) != equipment_token(
        identity.model
    )
