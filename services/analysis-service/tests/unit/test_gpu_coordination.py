# services/analysis-service/tests/unit/test_gpu_coordination.py

"""Unit tests Analysis global GPU coordination."""

import asyncio
from pathlib import Path

import pytest
from pdrd_analysis_service.application.ports.gpu import (
    GpuMemorySnapshot,
)
from pdrd_analysis_service.infrastructure.gpu_coordination import (
    CrossProcessFileGpuLease,
    WaitingGpuCoordinator,
)

GIB = 1024**3


class FakeGpuProbe:
    """Возвращает controlled VRAM snapshots."""

    def __init__(
        self,
        values: list[int],
    ) -> None:
        """Сохраняет последовательность VRAM snapshots."""
        self._values = values

        self.calls = 0

    async def snapshot(
        self,
    ) -> GpuMemorySnapshot:
        """Возвращает следующий snapshot."""
        index = min(
            self.calls,
            len(
                self._values,
            )
            - 1,
        )

        self.calls += 1

        return GpuMemorySnapshot(
            cuda_available=True,
            free_vram_bytes=self._values[index],
            total_vram_bytes=24 * GIB,
        )


async def test_coordinator_waits_until_vram_is_free(
    tmp_path: Path,
) -> None:
    """Admission не пропускает model load на занятую VRAM."""
    probe = FakeGpuProbe(
        [
            8 * GIB,
            10 * GIB,
            15 * GIB,
        ]
    )

    coordinator = WaitingGpuCoordinator(
        lease=CrossProcessFileGpuLease(
            path=tmp_path / "gpu.lock",
            poll_seconds=0.01,
        ),
        memory_probe=probe,
        lease_timeout_seconds=2,
        admission_poll_seconds=0.01,
    )

    async with coordinator.reserve(
        required_free_vram_bytes=12 * GIB,
    ) as snapshot:
        assert snapshot.free_vram_bytes == 15 * GIB

    assert probe.calls == 3


async def test_file_gpu_lease_blocks_second_owner(
    tmp_path: Path,
) -> None:
    """Два независимых lease объекта сериализуются одним file."""
    path = tmp_path / "gpu.lock"

    first = CrossProcessFileGpuLease(
        path=path,
        poll_seconds=0.01,
    )

    second = CrossProcessFileGpuLease(
        path=path,
        poll_seconds=0.01,
    )

    await first.acquire(
        timeout_seconds=1,
    )

    second_task = asyncio.create_task(
        second.acquire(
            timeout_seconds=1,
        )
    )

    await asyncio.sleep(
        0.05,
    )

    assert not second_task.done()

    await first.release()

    await asyncio.wait_for(
        second_task,
        timeout=1,
    )

    await second.release()


async def test_gpu_lease_released_after_exception(
    tmp_path: Path,
) -> None:
    """Exception внутри model stage не оставляет global lock."""
    path = tmp_path / "gpu.lock"

    probe = FakeGpuProbe(
        [
            24 * GIB,
        ]
    )

    coordinator = WaitingGpuCoordinator(
        lease=CrossProcessFileGpuLease(
            path=path,
            poll_seconds=0.01,
        ),
        memory_probe=probe,
        lease_timeout_seconds=1,
        admission_poll_seconds=0.01,
    )

    with pytest.raises(
        RuntimeError,
        match="boom",
    ):
        async with coordinator.reserve(
            required_free_vram_bytes=12 * GIB,
        ):
            raise RuntimeError(
                "boom",
            )

    another = CrossProcessFileGpuLease(
        path=path,
        poll_seconds=0.01,
    )

    await another.acquire(
        timeout_seconds=0.5,
    )

    await another.release()
