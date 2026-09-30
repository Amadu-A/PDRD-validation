# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/reviewed_pdf_cache.py

"""Атомарный кеш: один файл на задание, ключ и PDF заменяются вместе."""

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LocalReviewedPdfCache:
    """Не затрагивает исходники, автоматические PDF и прежние preview."""

    root_path: Path

    async def load(self, *, job_id: UUID, key: str) -> bytes | None:
        """Читает только кеш точной проекции; повреждение приводит к перестроению."""

        def read():
            """Файловый ввод выполняется вне event loop."""
            try:
                data = (
                    self.root_path / str(job_id) / "reviewed-current.cache"
                ).read_bytes()
            except FileNotFoundError:
                return None
            prefix = key.encode("ascii") + b"\n"
            if not data.startswith(prefix):
                return None
            checksum, separator, content = data[len(prefix) :].partition(b"\n")
            return (
                content
                if separator
                and content.startswith(b"%PDF-")
                and hashlib.sha256(content).hexdigest().encode("ascii") == checksum
                else None
            )

        return await asyncio.to_thread(read)

    async def save(self, *, job_id: UUID, key: str, content: bytes) -> None:
        """Уникальный временный файл предотвращает смешивание параллельных экспортов."""

        def write():
            """Публикует полностью записанный кеш одной операцией os.replace."""
            folder = self.root_path / str(job_id)
            folder.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=folder, prefix="reviewed-", suffix=".tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(
                        key.encode("ascii")
                        + b"\n"
                        + hashlib.sha256(content).hexdigest().encode("ascii")
                        + b"\n"
                        + content
                    )
                os.replace(temporary, folder / "reviewed-current.cache")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        await asyncio.to_thread(write)
