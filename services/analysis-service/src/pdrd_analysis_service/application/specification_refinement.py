# services/analysis-service/src/pdrd_analysis_service/application/specification_refinement.py

"""Короткие текстовые уточнения неоднозначных строк без повторного извлечения всего листа."""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.core.observability import log_execution_time
from pdrd_analysis_service.domain.equipment_specification import SpecificationCancelled

_PROMPT = """Ты обрабатываешь строки спецификации инженерного проекта, уже извлечённые из PDF.
Извлеки все переданные позиции оборудования, изделий и материалов в JSON.
Для каждой позиции сохрани row_id, исходное наименование, марку, изготовителя,
единицу измерения, количество, шифр проекта и раздел спецификации.
Caption объединяет наименование, марку и изготовителя без потери информации.
Units — единица измерения; Quantity — количество; UserComment1 — шифр проекта;
Under — только явно указанная группа или раздел.
Не выдумывай отсутствующие значения. Неизвестные значения оставляй пустой строкой.
Не объединяй разные изделия. Не назначай производителю значение из догадки о модели.
Сохраняй исходные номера позиций. Возвращай только JSON без пояснений.
Исходные строки: """
_STRUCTURED_FIELDS = (
    "position",
    "name",
    "model",
    "variant",
    "manufacturer",
    "units",
    "quantity",
    "project_code",
    "under",
)
_FIELDS = (*_STRUCTURED_FIELDS, "Caption", "Units", "Quantity", "UserComment1", "Under")


@dataclass(frozen=True, slots=True)
class RefineSpecification:
    """Уточняет только неоднозначные строки через общий кэш и limiter существующего порта."""

    vision_model: StructuredVisionModel
    batch_size: int = 20
    batch_characters: int = 6000
    max_calls: int = 4
    num_predict: int = 2400

    @log_execution_time(operation="specification.refine")
    async def execute(
        self,
        specification: dict,
        *,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> list[dict]:
        """Оставляет исходные данные при отказе модели или неподтверждённом уточнении."""
        ambiguous = [
            item for item in specification["items"] if item.get("needs_review")
        ]
        batches, current, size = [], [], 0
        for item in ambiguous:
            length = len(json.dumps(_input_row(item), ensure_ascii=False))
            if length > self.batch_characters:
                specification["warnings"].append(
                    f"Строка {item['item_id']}: превышает размер короткого уточнения; исходные данные сохранены."
                )
                continue
            if current and (
                len(current) >= self.batch_size or size + length > self.batch_characters
            ):
                batches.append(current)
                current, size = [], 0
            current.append(item)
            size += length
        if current:
            batches.append(current)
        metrics = []
        for number, batch in enumerate(batches[: self.max_calls]):
            if should_stop is not None and await should_stop():
                raise SpecificationCancelled
            inputs = [_input_row(item) for item in batch]
            schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": len(batch),
                        "maxItems": len(batch),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                field: {"type": "string", "maxLength": 1200}
                                for field in ("row_id", *_FIELDS)
                            },
                            "required": ["row_id", *_FIELDS],
                        },
                    }
                },
                "required": ["items"],
            }
            try:
                result = await self.vision_model.generate_json(
                    prompt=_PROMPT + json.dumps(inputs, ensure_ascii=False),
                    schema=schema,
                    num_predict=self.num_predict,
                    seed=100,
                    stage=f"specification_rows:{specification['page']}:{number}",
                    image_bytes=None,
                )
            except VisionModelError:
                specification["warnings"].append(
                    "Уточнение строк недоступно; исходные позиции сохранены и требуют проверки."
                )
                continue
            metrics.append(result.metrics.as_dict())
            refinements = result.payload.get("items", [])
            by_id = {
                row.get("row_id"): row for row in refinements if isinstance(row, dict)
            }
            if len(refinements) != len(batch) or set(by_id) != {
                item["item_id"] for item in batch
            }:
                specification["warnings"].append(
                    "Уточнение изменило состав строк; исходные позиции сохранены."
                )
                continue
            for item in batch:
                candidate = by_id[item["item_id"]]
                if _grounded_refinement(item, candidate):
                    item["refinement_status"] = "source_grounded"
                else:
                    item["refinement_status"] = "rejected_unproven"
                    specification["warnings"].append(
                        f"Строка {item['item_id']}: неподтверждённое уточнение отклонено."
                    )
        if len(batches) > self.max_calls:
            specification["warnings"].append(
                "Лимит уточнения строк достигнут; оставшиеся позиции сохранены без изменений."
            )
        return metrics


def _same_text(left: str, right: str) -> bool:
    """Сравнивает только оформление пробелов, не позволяя модели добавлять сведения."""
    return isinstance(left, str) and re.sub(r"\s+", "", left) == re.sub(
        r"\s+", "", right
    )


def _input_row(item: dict) -> dict:
    """Передаёт небольшой набор исходных ячеек, не страницы и не всю спецификацию."""
    cells = [[cell.get("text") for cell in row] for row in item.get("raw_values", [])]
    return {
        "row_id": item["item_id"],
        **{key: item.get(key, "") for key in _FIELDS},
        "raw_cells": cells,
    }


def _grounded_refinement(item: dict, candidate: dict) -> bool:
    """Допускает заполнение пустого поля лишь по явной подписи в исходной ячейке."""
    labels = {
        "position": r"позиция|поз\.",
        "model": r"модель|марка|тип",
        "variant": r"исполнение",
        "manufacturer": r"завод-изготовитель|изготовитель|производитель",
        "units": r"единица измерения",
        "quantity": r"количество",
        "project_code": r"шифр проекта",
        "under": r"раздел|группа",
    }
    source = " ".join(
        str(cell.get("text") or "")
        for row in item.get("raw_values", [])
        for cell in row
    )
    source = re.sub(r"\s+", " ", source)
    updates = {}
    for field in _STRUCTURED_FIELDS:
        value, original = candidate.get(field, ""), item.get(field, "")
        if not isinstance(value, str):
            return False
        if _same_text(value, original):
            continue
        if original or not value or field not in labels:
            return False
        if not re.search(
            rf"(?:{labels[field]})\s*[:=]\s*{re.escape(value)}(?![\w-])", source, re.I
        ):
            return False
        updates[field] = value
    item.update(updates)
    item.update(
        Caption="; ".join(
            value
            for value in (
                item["name"],
                " ".join(
                    value for value in (item["model"], item.get("variant", "")) if value
                ),
                item["manufacturer"],
            )
            if value
        ),
        Units=item["units"],
        Quantity=item["quantity"],
        UserComment1=item["project_code"],
        Under=item["under"],
        needs_review=(not bool(item["position"] and item["name"]))
        if updates
        else item.get("needs_review", False),
    )
    return True
