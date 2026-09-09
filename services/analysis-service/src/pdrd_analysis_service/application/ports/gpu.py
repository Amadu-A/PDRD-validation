# services/analysis-service/src/pdrd_analysis_service/application/ports/gpu.py

"""Application ports координации единственного GPU."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol


class GpuCoordinationError(RuntimeError):
    """GPU нельзя безопасно предоставить model runtime."""


@dataclass(frozen=True, slots=True)
class GpuMemorySnapshot:
    """Текущее состояние GPU memory."""

    cuda_available: bool

    free_vram_bytes: int | None

    total_vram_bytes: int | None


class GpuMemoryProbe(Protocol):
    """Порт чтения фактической свободной VRAM."""

    async def snapshot(
        self,
    ) -> GpuMemorySnapshot:
        """Возвращает текущий GPU snapshot."""
        ...


class GpuLease(Protocol):
    """Межпроцессный exclusive GPU lease."""

    async def acquire(
        self,
        *,
        timeout_seconds: float,
    ) -> None:
        """Ожидает эксклюзивный GPU lease."""
        ...

    async def release(
        self,
    ) -> None:
        """Освобождает lease."""
        ...


class GpuCoordinator(Protocol):
    """Оркестрирует lease и VRAM admission."""

    def reserve(
        self,
        *,
        required_free_vram_bytes: int,
    ) -> AbstractAsyncContextManager[GpuMemorySnapshot]:
        """Возвращает async context безопасного model execution."""
        ...
