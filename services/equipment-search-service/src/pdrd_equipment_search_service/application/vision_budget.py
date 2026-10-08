# services/equipment-search-service/src/pdrd_equipment_search_service/application/vision_budget.py

"""Общий бюджет адресных VLM-вызовов одного задания Equipment Search."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EquipmentVisionBudget:
    """Расходуется всеми моделями задания до запуска нового GPU-запроса."""

    remaining: int = 2
    metrics: list[dict[str, Any]] = field(default_factory=list)

    def claim(self) -> bool:
        """Разрешает один вызов, если бюджет ещё не исчерпан."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True
