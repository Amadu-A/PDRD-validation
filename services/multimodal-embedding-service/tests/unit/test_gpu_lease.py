# services/multimodal-embedding-service/tests/unit/test_gpu_lease.py

"""Tests OS-level cross-process GPU lease."""

import multiprocessing
import time
from pathlib import Path

from pdrd_multimodal_embedding_service.gpu_lease import (
    CrossProcessFileGpuLease,
)


def _hold_gpu_lock(
    path: str,
    acquired: multiprocessing.Queue,
    hold_seconds: float,
) -> None:
    """Child process helper."""
    lease = CrossProcessFileGpuLease(
        path=Path(
            path,
        ),
        poll_seconds=0.01,
    )

    lease.acquire(
        timeout_seconds=5,
    )

    acquired.put(
        time.monotonic(),
    )

    time.sleep(
        hold_seconds,
    )

    lease.release()


def test_file_gpu_lease_serializes_real_processes(
    tmp_path: Path,
) -> None:
    """File lease работает между OS processes, не только tasks."""
    context = multiprocessing.get_context(
        "spawn",
    )

    queue = context.Queue()

    path = str(tmp_path / "gpu.lock")

    first = context.Process(
        target=_hold_gpu_lock,
        args=(
            path,
            queue,
            0.4,
        ),
    )

    second = context.Process(
        target=_hold_gpu_lock,
        args=(
            path,
            queue,
            0.01,
        ),
    )

    first.start()

    first_acquired = queue.get(
        timeout=5,
    )

    second.start()

    second_acquired = queue.get(
        timeout=5,
    )

    first.join(
        timeout=5,
    )

    second.join(
        timeout=5,
    )

    assert first.exitcode == 0

    assert second.exitcode == 0

    assert second_acquired - first_acquired >= 0.30
