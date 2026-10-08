# services/equipment-search-service/src/pdrd_equipment_search_service/application/search_budget.py

"""Общий бюджет внешних операций всех моделей задания."""

from dataclasses import dataclass


@dataclass(slots=True)
class EquipmentSearchBudget:
    """Не даёт каждой следующей модели заново расходовать лимиты поиска."""

    queries_remaining: int
    downloads_remaining: int

    def claim_query(self) -> bool:
        """Резервирует один поисковый запрос до его запуска."""
        if self.queries_remaining <= 0:
            return False
        self.queries_remaining -= 1
        return True

    def claim_download(self) -> bool:
        """Резервирует одну загрузку до DNS и HTTP."""
        if self.downloads_remaining <= 0:
            return False
        self.downloads_remaining -= 1
        return True
