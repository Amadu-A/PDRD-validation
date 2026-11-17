# services/document-service/src/pdrd_document_service/infrastructure/extraction_artifacts.py

"""Атомарные артефакты внешнего извлечения в собственном томе Document Service."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

from pdrd_document_service.application.ports.specification_extraction import (
    ExtractionArtifactStorageError,
)


class FilesystemExtractionArtifacts:
    """Изолирует кэш интеграций по детерминированному SHA256 запроса."""

    def __init__(self, root: Path) -> None:
        """Сохраняет путь собственного тома PDRD."""
        self.root = root

    async def check_ready(self) -> None:
        """Проверяет запись и fsync, включая отсутствующий каталог и read-only том."""

        def probe():
            """Создаёт и удаляет только собственный временный файл без изменения артефактов."""
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.root) as stream:
                stream.write(b"pdrd-storage-probe")
                stream.flush()
                os.fsync(stream.fileno())

        await self._io(probe)

    @staticmethod
    async def _io(operation):
        """Отделяет эксплуатационный сбой файловой системы от программной ошибки/повреждения."""
        try:
            return await asyncio.to_thread(operation)
        except OSError as error:
            raise ExtractionArtifactStorageError(
                "Хранилище результатов извлечения недоступно."
            ) from error

    def _path(self, key: str) -> Path:
        """Не допускает произвольных путей из внешнего job_id."""
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Неверный ключ артефакта извлечения.")
        return self.root / (key + ".json")

    async def load(self, key: str) -> dict | None:
        """Проверяет digest перед использованием материализованного результата."""
        path = self._path(key)

        def read():
            """Читает полный атомарный конверт без блокировки event loop."""
            if not path.is_file():
                return None
            envelope = json.loads(path.read_text(encoding="utf-8"))
            raw = envelope["payload"]
            if hashlib.sha256(raw.encode()).hexdigest() != envelope["sha256"]:
                raise RuntimeError("Повреждён артефакт внешнего извлечения.")
            return json.loads(raw)

        return await self._io(read)

    async def save(self, key: str, value: dict) -> None:
        """Записывает через отдельный временный файл и атомарную замену."""
        path = self._path(key)
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        envelope = json.dumps(
            {"payload": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()},
            ensure_ascii=False,
        )

        def write():
            """Сохраняет готовый конверт и удаляет только свой незавершённый файл."""
            self.root.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(envelope)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        await self._io(write)
