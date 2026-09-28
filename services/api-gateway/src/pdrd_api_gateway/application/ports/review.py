# services/api-gateway/src/pdrd_api_gateway/application/ports/review.py

"""Контракты контекста инженера и отдельного сервиса Human Review.

Gateway проверяет доступ к заданию. Experience владеет решениями, ревизиями,
геометрией и журналом. Сервисы не импортируют пакеты друг друга.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

ReviewOperation = Literal["open", "read", "command"]


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Доверенный серверный субъект, задание и запрошенная операция."""

    actor: str
    job_id: UUID
    operation: ReviewOperation


class ReviewContextProvider(Protocol):
    """Точка будущего подключения идентичности; без браузерного actor."""

    def resolve(self, *, job_id: UUID, operation: ReviewOperation) -> ReviewContext:
        """Возвращает серверный контекст либо запрещает операцию."""
        ...


class ReviewAccessPolicy(Protocol):
    """Точка подключения будущей проверки прав на задание и операцию."""

    async def require(self, context: ReviewContext) -> None:
        """Разрешает доступ или сообщает об отказе до обращения к Experience."""
        ...


class ReviewService(Protocol):
    """Порт отдельного Experience Service, без предположений о HTTP/SQL."""

    async def execute(
        self, *, context: ReviewContext, command: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Возвращает авторитетный снимок после чтения или команды."""
        ...


class ReviewRequestError(Exception):
    """Ожидаемая ошибка операции, которую transport переводит в HTTP."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Сохраняет только безопасное публичное описание."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
