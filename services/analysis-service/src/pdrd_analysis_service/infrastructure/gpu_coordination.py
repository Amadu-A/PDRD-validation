# services/analysis-service/src/pdrd_analysis_service/infrastructure/gpu_coordination.py

"""Cross-process GPU lease и VRAM admission Analysis Service."""

import asyncio
import errno
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO

import httpx

from pdrd_analysis_service.application.ports.gpu import (
    GpuCoordinationError,
    GpuMemoryProbe,
    GpuMemorySnapshot,
)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class CrossProcessFileGpuLease:
    """Advisory file lock, общий для процессов и Docker containers."""

    def __init__(
        self,
        *,
        path: Path,
        poll_seconds: float,
    ) -> None:
        """Сохраняет lock path."""
        self._path = path

        self._poll_seconds = poll_seconds

        self._handle: BinaryIO | None = None

        self._state_lock = threading.Lock()

    async def acquire(
        self,
        *,
        timeout_seconds: float,
    ) -> None:
        """Захватывает file lock без блокировки asyncio event loop."""
        await asyncio.to_thread(
            self._acquire_sync,
            timeout_seconds,
        )

    async def release(
        self,
    ) -> None:
        """Освобождает file lock."""
        await asyncio.to_thread(
            self._release_sync,
        )

    def _acquire_sync(
        self,
        timeout_seconds: float,
    ) -> None:
        """Blocking acquire с bounded timeout."""
        deadline = time.monotonic() + timeout_seconds

        self._path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        handle = self._path.open(
            "a+b",
        )

        handle.seek(
            0,
            os.SEEK_END,
        )

        if handle.tell() == 0:
            handle.write(
                b"\0",
            )

            handle.flush()

        while True:
            try:
                self._try_lock(
                    handle,
                )

            except OSError as error:
                if error.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                }:
                    handle.close()

                    raise

                if time.monotonic() >= deadline:
                    handle.close()

                    raise GpuCoordinationError(
                        "Истёк timeout ожидания global GPU lease.",
                    ) from error

                time.sleep(
                    self._poll_seconds,
                )

                continue

            with self._state_lock:
                if self._handle is not None:
                    self._unlock(
                        handle,
                    )

                    handle.close()

                    raise GpuCoordinationError(
                        "GPU lease уже захвачен этим runtime.",
                    )

                self._handle = handle

            return

    def _release_sync(
        self,
    ) -> None:
        """Blocking release."""
        with self._state_lock:
            handle = self._handle

            self._handle = None

        if handle is None:
            return

        try:
            self._unlock(
                handle,
            )

        finally:
            handle.close()

    @staticmethod
    def _try_lock(
        handle: BinaryIO,
    ) -> None:
        """Пытается захватить OS lock."""
        handle.seek(
            0,
        )

        if os.name == "nt":
            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )

        else:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

    @staticmethod
    def _unlock(
        handle: BinaryIO,
    ) -> None:
        """Освобождает OS lock."""
        handle.seek(
            0,
        )

        if os.name == "nt":
            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )

        else:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_UN,
            )


class HttpGpuMemoryProbe:
    """Читает VRAM через lightweight status embedding service."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        """Сохраняет transport settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._timeout_seconds = timeout_seconds

    async def snapshot(
        self,
    ) -> GpuMemorySnapshot:
        """Возвращает status, не загружая embedding checkpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/internal/v1/status",
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise GpuCoordinationError(
                "Не удалось получить GPU memory status.",
            ) from error

        payload = response.json()

        return GpuMemorySnapshot(
            cuda_available=bool(
                payload.get(
                    "cuda_available",
                    False,
                )
            ),
            free_vram_bytes=self._optional_int(
                payload.get(
                    "free_vram_bytes",
                )
            ),
            total_vram_bytes=self._optional_int(
                payload.get(
                    "total_vram_bytes",
                )
            ),
        )

    @staticmethod
    def _optional_int(
        value: object,
    ) -> int | None:
        """Нормализует optional integer."""
        try:
            if value is None:
                return None

            return int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None


class WaitingGpuCoordinator:
    """Сериализует PDRD models и ждёт admission после lease."""

    def __init__(
        self,
        *,
        lease: CrossProcessFileGpuLease,
        memory_probe: GpuMemoryProbe,
        lease_timeout_seconds: float,
        admission_poll_seconds: float,
    ) -> None:
        """Сохраняет dependencies."""
        self._lease = lease

        self._memory_probe = memory_probe

        self._lease_timeout_seconds = lease_timeout_seconds

        self._admission_poll_seconds = admission_poll_seconds

    @asynccontextmanager
    async def reserve(
        self,
        *,
        required_free_vram_bytes: int,
    ) -> AsyncIterator[GpuMemorySnapshot]:
        """Получает lease и ждёт достаточную VRAM."""
        await self._lease.acquire(
            timeout_seconds=self._lease_timeout_seconds,
        )

        try:
            deadline = asyncio.get_running_loop().time() + self._lease_timeout_seconds

            while True:
                snapshot = await self._memory_probe.snapshot()

                if not snapshot.cuda_available:
                    raise GpuCoordinationError(
                        "CUDA недоступна.",
                    )

                free_vram = snapshot.free_vram_bytes

                if free_vram is not None and free_vram >= required_free_vram_bytes:
                    yield snapshot

                    return

                if asyncio.get_running_loop().time() >= deadline:
                    raise GpuCoordinationError(
                        "Истёк timeout ожидания свободной VRAM: "
                        f"available={free_vram}, "
                        f"required={required_free_vram_bytes}.",
                    )

                await asyncio.sleep(
                    self._admission_poll_seconds,
                )

        finally:
            await self._lease.release()
