# services/analysis-service/src/pdrd_analysis_service/application/origin_history.py

"""История объединений без повторного вложения одних и тех же доказательств."""

import hashlib
import json

_FORMAT = "pdrd.origin-history.v1"


def _digest(record: dict) -> str:
    """Идентифицирует полную запись с учётом значений, порядка массивов и ссылок."""
    return hashlib.sha256(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def compact_origin_history(origins: tuple[dict, ...]) -> tuple[dict, ...]:
    """Сохраняет каждую уникальную исходную запись и все связи объединений.

    Корневые записи остаются на прежнем месте для действующих evidence gates.
    Только вложенные origin_assertions заменяются ссылками на полные записи
    в origin_history.records. Модель, источники, области и статусы не изменяются.
    Повторная обработка уже компактной истории идемпотентна; висячая ссылка
    или повреждённая запись вызывает ошибку вместо тихой потери происхождения.
    """
    records = {}
    memo = {}
    active = set()
    has_references = False

    def remember(record):
        """Добавляет полное содержимое один раз, не объединяя разные события."""
        key = _digest(record)
        if key in records and records[key] != record:
            raise ValueError("Коллизия идентификатора истории происхождения.")
        records.setdefault(key, record)
        return key

    def import_table(table):
        """Принимает предыдущий реестр вместе с его неизменными идентификаторами."""
        nonlocal has_references
        if table.get("format") != _FORMAT or not isinstance(table.get("records"), dict):
            raise ValueError("Неизвестный формат истории происхождения.")
        has_references = True
        for key, record in table["records"].items():
            if not isinstance(record, dict) or _digest(record) != key:
                raise ValueError("Повреждена запись истории происхождения.")
            if key in records and records[key] != record:
                raise ValueError("Коллизия записи истории происхождения.")
            records.setdefault(key, record)

    def register(record):
        """Учитывает совместно используемые Python-объекты до их сериализации в JSON."""
        identity = id(record)
        if identity in memo:
            return memo[identity]
        if identity in active:
            raise ValueError("Цикл во вложенной истории происхождения.")
        active.add(identity)
        try:
            value = normalize(record)
            key = remember(value)
            memo[identity] = key
            return key
        finally:
            active.remove(identity)

    def origin_refs(items):
        """Сохраняет порядок и повторные ссылки внутри конкретного снимка замечания."""
        nonlocal has_references
        result = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Исходная история должна содержать записи-объекты.")
            if item.get("origin") == "origin_history":
                import_table(item)
            elif set(item) == {"origin_ref"}:
                has_references = True
                result.append(dict(item))
            else:
                has_references = True
                result.append({"origin_ref": register(item)})
        return result

    def normalize(value):
        """Не меняет доказательства; сокращает только вложенную историю."""
        if isinstance(value, dict):
            return {
                key: origin_refs(item)
                if key == "origin_assertions" and isinstance(item, (tuple, list))
                else normalize(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        return value

    root_keys = []
    for origin in origins:
        if origin.get("origin") == "origin_history":
            import_table(origin)
        else:
            key = register(origin)
            if key not in root_keys:
                root_keys.append(key)

    def check_references(value):
        """Проверяет замкнутость реестра, включая ранее сохранённые записи."""
        if isinstance(value, dict):
            if set(value) == {"origin_ref"} and value["origin_ref"] not in records:
                raise ValueError("Не найдена запись по ссылке истории происхождения.")
            for item in value.values():
                check_references(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                check_references(item)

    for record in records.values():
        check_references(record)
    roots = tuple(records[key] for key in root_keys)
    if not has_references:
        return roots
    return (*roots, {"origin": "origin_history", "format": _FORMAT, "records": records})
