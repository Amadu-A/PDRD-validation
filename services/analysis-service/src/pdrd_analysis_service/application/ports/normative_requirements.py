# services/analysis-service/src/pdrd_analysis_service/application/ports/normative_requirements.py

"""Чтение полного версионированного нормативного корпуса через границу Knowledge."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


class NormativeRequirementsUnavailable(RuntimeError):
    """Источник недоступен или нарушает контракт; старые маршруты остаются доступны."""


@dataclass(frozen=True, slots=True)
class AtomicNormativeDocument:
    """Полное поколение одного выбранного документа и его проверяемый манифест."""

    requirements: tuple[dict, ...]
    manifest: dict


class NormativeRequirementsReader(Protocol):
    """Не использует embedding/top-K и не создаёт новое поколение при чтении."""

    async def load(
        self,
        *,
        section_id: str,
        document_id: str,
        generation_id: str | None,
        assessed_on: str,
        check_cancellation: Callable[[], Awaitable[None]],
    ) -> AtomicNormativeDocument:
        """Закрепляет поколение первой страницы и возвращает все атомарные требования."""
        ...
