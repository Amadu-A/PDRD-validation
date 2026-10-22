# services/analysis-service/src/pdrd_analysis_service/transport/http/context_pool.py

"""Восстановление транспортного словаря строк до проверки исходных HTTP-схем."""

from copy import deepcopy

_CONTEXT_FIELDS = frozenset(
    {
        "extracted_text",
        "page_facts",
        "normative_sources",
        "technical_assignment_sources",
        "conflict_candidates",
        "user_package_sources",
        "document_context_sources",
        "normative_system_prompt",
        "project_context_sources",
    }
)
_MAX_REFS = 100_000
_MAX_RESTORED_CHARACTERS = 128 * 1024 * 1024


def restore_context_pool(value):
    """Восстанавливает только явно перечисленные строковые позиции без интерпретации текста.

    Старый несжатый контракт остаётся допустимым. Некорректная упаковка завершается
    ошибкой валидации; она не даёт частичных источников и не вызывает модель.
    """
    if not isinstance(value, dict) or not any(
        key in value for key in ("context_encoding", "context_pool", "context_refs")
    ):
        return value
    if value.get("context_encoding") != "text-pool-v1":
        raise ValueError("Неизвестная упаковка контекста.")
    pool, references = value.get("context_pool"), value.get("context_refs")
    if not isinstance(pool, list) or not all(isinstance(row, str) for row in pool):
        raise ValueError("Словарь контекста должен содержать строки.")
    if not isinstance(references, list) or len(references) > _MAX_REFS:
        raise ValueError("Некорректный список ссылок контекста.")
    result = deepcopy(value)
    for key in ("context_encoding", "context_pool", "context_refs"):
        result.pop(key)
    seen, restored = set(), 0
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"path", "index"}:
            raise ValueError("Некорректная ссылка контекста.")
        path, index = reference["path"], reference["index"]
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(pool)
            or not isinstance(path, list)
            or not 3 <= len(path) <= 32
            or not all(
                isinstance(part, (str, int)) and not isinstance(part, bool)
                for part in path
            )
            or path[0] != "items"
            or path[2] not in _CONTEXT_FIELDS
        ):
            raise ValueError("Ссылка контекста выходит за допустимую область.")
        identity = tuple(path)
        if identity in seen:
            raise ValueError("Повторная ссылка на одну позицию контекста.")
        seen.add(identity)
        target = result
        for part in path[:-1]:
            target = _child(target, part)
        if _child(target, path[-1]) is not None:
            raise ValueError("Ссылка контекста перезаписывает исходное значение.")
        restored += len(pool[index])
        if restored > _MAX_RESTORED_CHARACTERS:
            raise ValueError("Восстановленный контекст превышает транспортный предел.")
        target[path[-1]] = pool[index]
    return result


def _child(value, key):
    """Не допускает отрицательных индексов и не обращается к атрибутам объектов."""
    if isinstance(value, dict) and isinstance(key, str) and key in value:
        return value[key]
    if (
        isinstance(value, list)
        and isinstance(key, int)
        and not isinstance(key, bool)
        and 0 <= key < len(value)
    ):
        return value[key]
    raise ValueError("Позиция ссылки отсутствует в контексте.")
