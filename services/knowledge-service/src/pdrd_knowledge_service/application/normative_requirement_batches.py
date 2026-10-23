# services/knowledge-service/src/pdrd_knowledge_service/application/normative_requirement_batches.py

"""Ограничение размера JSONB без обрезки нормативного атома или его источников."""

import json

MAX_ATOM_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
WRITE_ATTEMPTS = 3
WRITE_BACKOFF_SECONDS = 0.5


class NormativeAtomTooLargeError(ValueError):
    """Один атом нельзя записать в допустимом объёме; автоматический повтор бесполезен."""


class NormativeBatchWriteFailedError(ValueError):
    """Ограниченные повторы порции исчерпаны; требуется диагностика до нового запуска."""


def bounded_requirement_batches(requirements):
    """Делит уже ограниченную по числу порцию по реальному размеру JSON-параметров.

    SQLAlchemy использует json.dumps по умолчанию, включая ASCII-экранирование.
    Лимит относится к параметрам JSONB; SQL и сетевое обрамление имеют свой объём.
    """
    batch, size = [], 0
    for item in requirements:
        item_size = len(json.dumps(item.as_dict(), allow_nan=False).encode("utf-8"))
        if item_size > MAX_ATOM_BYTES:
            raise NormativeAtomTooLargeError(
                f"normative_atom_too_large: requirement_id={item.requirement_id}; "
                f"json_bytes={item_size}; limit={MAX_ATOM_BYTES}. "
                "Атом не обрезан; требуется проверить исходное извлечение."
            )
        if batch and size + item_size > MAX_BATCH_BYTES:
            yield tuple(batch), size
            batch, size = [], 0
        batch.append(item)
        size += item_size
    if batch:
        yield tuple(batch), size
