# services/equipment-search-service/src/pdrd_equipment_search_service/application/search_budget.py

"""Общий бюджет внешних операций всех моделей задания."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(slots=True)
class EquipmentSearchBudget:
    """Не даёт каждой следующей модели заново расходовать лимиты поиска."""

    queries_remaining: int
    downloads_remaining: int
    pages_remaining: int = 3
    extractions_remaining: int = 5
    on_progress: Callable[[dict], Awaitable[None]] | None = None
    download_attempts: int = 0

    def claim_page(self) -> bool:
        """Резервирует статическую страницу до обращения к сети."""
        if self.pages_remaining <= 0:
            return False
        self.pages_remaining -= 1
        return True

    def claim_extraction(self) -> bool:
        """Ограничивает число разборов документа независимо от числа ссылок."""
        if self.extractions_remaining <= 0:
            return False
        self.extractions_remaining -= 1
        return True

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
