# services/analysis-service/src/pdrd_analysis_service/application/understanding_recovery.py

"""Восстанавливает переполненный Understanding отдельными ограниченными ответами."""

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace

from pdrd_analysis_service.application.json_schemas import build_page_facts_schema
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.prompts import build_page_understanding_prompt
from pdrd_analysis_service.domain.analysis import GenerationMetrics
from pdrd_analysis_service.domain.equipment_specification import SpecificationCancelled


async def recover_page_understanding(
    model: StructuredVisionModel,
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
    image_bytes: bytes,
    num_predict: int,
    max_facts: int,
    max_equipment: int,
    initial_error: VisionModelOutputBudgetError,
    should_stop: Callable[[], Awaitable[bool]] | None = None,
    prompt_suffix: str = "",
) -> tuple[dict, GenerationMetrics]:
    """Разделяет основной ответ, D и EQ; лимиты фактов и правила идентификации сохраняет."""
    metrics = [initial_error.metrics] if initial_error.metrics is not None else []
    calls = 0

    async def generate(prompt: str, schema: dict, suffix: str):
        """Проверяет отмену до каждого восстановления и не принимает обрезанный JSON."""
        nonlocal calls
        if should_stop is not None and await should_stop():
            raise SpecificationCancelled
        calls += 1
        try:
            result = await model.generate_json(
                prompt=prompt,
                schema=schema,
                num_predict=num_predict,
                seed=100 + calls,
                stage=f"page_understanding:{page_number}:recovery:{suffix}",
                image_bytes=image_bytes,
            )
        except VisionModelOutputBudgetError as error:
            if error.metrics is not None:
                metrics.append(error.metrics)
            raise
        metrics.append(result.metrics)
        return result

    def prompt(facts: int = 0, equipment: int = 0):
        """Оставляет исходные инструкции grounding и исходный контекст изображения."""
        return (
            build_page_understanding_prompt(
                page_number=page_number,
                heuristic_page_type=heuristic_page_type,
                extracted_text=extracted_text,
                max_facts=facts,
                max_equipment=equipment,
            )
            + prompt_suffix
        )

    core_schema = build_page_facts_schema(0, 0)
    split_core = not (max_facts or max_equipment)
    payload = {}
    if not split_core:
        try:
            core = await generate(prompt(), core_schema, "core")
            split_core = core.metrics.done_reason == "length"
            payload = dict(core.payload)
        except VisionModelOutputBudgetError:
            split_core = True
    if split_core:
        # Если переполнились даже лёгкие поля, делим ответ по полям, сохраняя их ёмкость.
        payload = {}
        groups = (
            ("discipline", "page_type", "summary"),
            ("objects",),
            ("connections",),
            ("labels",),
            ("normative_queries",),
        )
        for index, fields in enumerate(groups):
            schema = deepcopy(core_schema)
            schema["properties"] = {name: schema["properties"][name] for name in fields}
            schema["required"] = list(fields)
            result = await generate(
                prompt() + "\nВ этом вызове верни только поля переданной схемы.",
                schema,
                f"core-fields-{index}",
            )
            if result.metrics.done_reason == "length":
                raise VisionModelOutputBudgetError(
                    "Поле листа достигло output budget.", metrics=result.metrics
                )
            payload.update(result.payload)

    for field, limit, size in (
        ("document_facts", max_facts, 4),
        ("equipment_identities", max_equipment, 8),
    ):
        if limit <= 0:
            continue
        records = []
        keys = set()
        round_index = 0
        capacity_limit = size
        while len(records) < limit:
            capacity = min(capacity_limit, limit - len(records))
            schema = build_page_facts_schema(
                capacity if field == "document_facts" else 0,
                capacity if field == "equipment_identities" else 0,
            )
            schema["properties"] = {field: schema["properties"][field]}
            schema["required"] = [field]
            instruction = (
                f"\nВ этом вызове верни только {field}, до {capacity} новых записей."
                "\nНе повторяй уже найденные записи. Сохраняй разные объекты и исходные доказательства."
                "\nЕсли других записей нет, верни пустой массив."
                "\nУже найдено:\n"
                + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
            )
            try:
                result = await generate(
                    prompt(
                        capacity if field == "document_facts" else 0,
                        capacity if field == "equipment_identities" else 0,
                    )
                    + instruction,
                    schema,
                    f"{field}:{round_index}:size{capacity}",
                )
            except VisionModelOutputBudgetError:
                if capacity <= 1:
                    raise
                capacity_limit = max(1, capacity // 2)
                continue
            round_index += 1
            batch = result.payload.get(field)
            if not isinstance(batch, list) or len(batch) > capacity:
                raise ValueError(f"Некорректная порция {field}.")
            added = 0
            for record in batch:
                if not isinstance(record, dict):
                    raise ValueError(f"Некорректная запись {field}.")
                key = json.dumps(record, sort_keys=True, ensure_ascii=False)
                if key not in keys:
                    keys.add(key)
                    records.append(record)
                    added += 1
            if len(records) >= limit:
                break
            if result.metrics.done_reason == "length":
                if capacity <= 1:
                    raise VisionModelOutputBudgetError(
                        f"Единичная запись {field} достигла output budget.",
                        metrics=result.metrics,
                    )
                capacity_limit = max(1, capacity // 2)
                continue
            if len(batch) < capacity or not added:
                break
        payload[field] = records

    last = metrics[-1]

    def total(name):
        """Суммирует известные счётчики, не превращая отсутствие метрик в ноль."""
        values = [
            getattr(item, name) for item in metrics if getattr(item, name) is not None
        ]
        return sum(values) if values else None

    return payload, replace(
        last,
        attempt=max(item.attempt for item in metrics),
        requested_num_predict=total("requested_num_predict"),
        total_duration_ms=total("total_duration_ms"),
        load_duration_ms=total("load_duration_ms"),
        prompt_eval_count=total("prompt_eval_count"),
        eval_count=total("eval_count"),
        content_length=total("content_length"),
        thinking_length=total("thinking_length"),
    )
