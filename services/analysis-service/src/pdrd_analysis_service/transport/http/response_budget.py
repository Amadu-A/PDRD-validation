# services/analysis-service/src/pdrd_analysis_service/transport/http/response_budget.py

"""Потоковое измерение DTO до создания большой строки сериализованного ответа."""

import asyncio
import json

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from pdrd_analysis_service.application.stage_transport import STAGE_RESPONSE_BYTES


def _value(value):
    """Раскрывает один DTO и его вычисляемые поля без рекурсивной копии всего ответа."""
    if isinstance(value, BaseModel):
        return {
            **dict(value),
            **{key: getattr(value, key) for key in type(value).model_computed_fields},
        }
    return to_jsonable_python(value)


async def response_bytes(response, budget=STAGE_RESPONSE_BYTES):
    """Возвращает точные bytes малого ответа либо нижнюю границу после превышения бюджета."""

    def measure():
        size = 0
        encoder = json.JSONEncoder(
            ensure_ascii=False, separators=(",", ":"), default=_value
        )
        for piece in encoder.iterencode(response):
            size += len(piece.encode("utf-8"))
            if size > budget:
                break
        return size

    return await asyncio.to_thread(measure)
