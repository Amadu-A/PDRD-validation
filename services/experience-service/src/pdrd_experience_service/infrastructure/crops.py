# services/experience-service/src/pdrd_experience_service/infrastructure/crops.py

"""Свой атомарный PNG-store и внутренний HTTP-клиент Document Service.

Образы адресуются SHA256: браузер не задаёт путь или URL источника.
Запись перед транзакцией БД может оставить повторно используемый orphan PNG;
физическое удаление и сборка мусора выполняются отдельно от CRUD каталога.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from pdrd_experience_service.domain.catalog import Crop
from pdrd_experience_service.domain.review import Rectangle

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CROP_BYTES = 12_000_000


def crop_metadata(content: bytes) -> Crop:
    """Проверяет PNG/IHDR и разумные размеры до записи или выдачи."""
    if (
        not 33 <= len(content) <= MAX_CROP_BYTES
        or not content.startswith(PNG_SIGNATURE)
        or content[12:16] != b"IHDR"
    ):
        raise RuntimeError("Хранилище crop получило некорректный PNG.")
    width, height = struct.unpack(">II", content[16:24])
    if not 1 <= width <= 2000 or not 1 <= height <= 2000:
        raise RuntimeError("Размеры PNG превышают ограничения crop.")
    return Crop(hashlib.sha256(content).hexdigest(), width, height)


@dataclass(frozen=True, slots=True)
class LocalCropStore:
    """Атомарные PNG в постоянном volume Experience, без хранения листов."""

    root: Path

    def _path(self, digest: str) -> Path:
        """Разрешает только свой фиксированный адрес контента."""
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise RuntimeError("Некорректный адрес crop.")
        return self.root / digest[:2] / f"{digest}.png"

    def _write(self, content: bytes, crop: Crop) -> None:
        """Замена файла не оставляет частичных байтов при параллельной записи."""
        path = self._path(crop.sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".crop-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Атомарная публикация неизменяемого контента без перезаписи
                # существующего файла; работает при конкуренции и на Windows.
                os.link(name, path)
            except FileExistsError:
                if crop_metadata(path.read_bytes()) != crop:
                    raise RuntimeError("Существующий crop повреждён.") from None
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def put(self, content: bytes) -> Crop:
        """Возвращает только проверенные размеры и хеш PNG."""
        crop = crop_metadata(content)
        await asyncio.to_thread(self._write, content, crop)
        return crop

    async def read(self, crop: Crop) -> bytes:
        """Отсутствующий или изменённый crop не выдаётся за обучающий пример."""
        content = await asyncio.to_thread(self._path(crop.sha256).read_bytes)
        if crop_metadata(content) != crop:
            raise RuntimeError("Целостность crop нарушена.")
        return content


@dataclass(frozen=True, slots=True)
class DocumentCropRenderer:
    """Передаёт проверенный исходник в Document Service без браузерных URL."""

    base_url: str
    transport: httpx.AsyncBaseTransport | None = None

    async def render(
        self, *, pdf: bytes, page_number: int, regions: tuple[Rectangle, ...]
    ) -> tuple[bytes, ...]:
        """HTTP не следует редиректам; число и размер PNG проверяются до сохранения."""
        try:
            async with httpx.AsyncClient(
                timeout=300, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/internal/v1/pdf/crop",
                    files={"file": ("original.pdf", pdf, "application/pdf")},
                    data={
                        "specification": json.dumps(
                            {
                                "page_number": page_number,
                                "regions": [asdict(box) for box in regions],
                            }
                        )
                    },
                )
            response.raise_for_status()
            payload = response.json()
            images = payload["images"]
            if not isinstance(images, list) or len(images) != len(regions):
                raise ValueError("Неполный ответ crop.")
            decoded = []
            for encoded in images:
                if (
                    not isinstance(encoded, str)
                    or len(encoded) > MAX_CROP_BYTES * 4 // 3 + 4
                ):
                    raise ValueError("Некорректный размер crop.")
                content = base64.b64decode(encoded, validate=True)
                crop_metadata(content)
                decoded.append(content)
            return tuple(decoded)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            raise RuntimeError("Document Service не смог подготовить crop.") from error
