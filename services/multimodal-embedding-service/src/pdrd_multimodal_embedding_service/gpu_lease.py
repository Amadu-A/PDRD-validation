# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/gpu_lease.py

"""Cross-process file lease единственного GPU."""

import errno
import os
import threading
import time
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class GpuLeaseTimeoutError(RuntimeError):
    """Global GPU lease не получен за configured timeout."""


class CrossProcessFileGpuLease:
    """OS advisory lock, общий между PDRD containers."""

    def __init__(
        self,
        *,
        path: Path,
        poll_seconds: float,
    ) -> None:
        """Сохраняет lock configuration."""
        self._path = path

        self._poll_seconds = poll_seconds

        self._handle: BinaryIO | None = None

        self._state_lock = threading.Lock()

    @property
    def acquired(
        self,
    ) -> bool:
        """Показывает, удерживает ли runtime lease."""
        with self._state_lock:
            return self._handle is not None

    def acquire(
        self,
        *,
        timeout_seconds: float,
    ) -> None:
        """Blocking bounded acquire."""
        if self.acquired:
            return

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

                    raise GpuLeaseTimeoutError(
                        "Истёк timeout ожидания global GPU lease.",
                    ) from error

                time.sleep(
                    self._poll_seconds,
                )

                continue

            with self._state_lock:
                self._handle = handle

            return

    def release(
        self,
    ) -> None:
        """Освобождает OS lease."""
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
        """Пытается захватить advisory lock."""
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
        """Освобождает advisory lock."""
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
