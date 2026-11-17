# services/document-service/src/pdrd_document_service/infrastructure/docling.py

"""Адаптер фактического Docling Serve 1.38.2: выборочный PDF, async status/result и собственные артефакты."""

import asyncio
import hashlib
import json

import fitz
import httpx

from pdrd_document_service.application.ports.document_conversion import (
    DocumentConversionError,
)


class HttpDoclingConverter:
    """Использует CPU-конверсию выбранного листа с конечным серверным сроком обработки."""

    def __init__(self, *, settings, artifacts, transport=None) -> None:
        """Сохраняет выделенную конфигурацию и независимое от внешнего TTL хранилище."""
        self.settings, self.artifacts, self.transport = settings, artifacts, transport

    async def convert(self, *, content: bytes, page_number: int, do_ocr: bool) -> dict:
        """Не повторяет создание неизвестного задания после потери ответа на POST."""
        identity = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "page": page_number,
            "do_ocr": do_ocr,
            "ocr_preset": self.settings.ocr_preset,
            "ocr_languages": list(self.settings.ocr_languages),
            "version": self.settings.cache_version,
            "provider": self.settings.base_url,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cached = await self.artifacts.load(key)
        if cached is not None:
            return cached
        with (
            fitz.open(stream=content, filetype="pdf") as source,
            fitz.open() as selected,
        ):
            if not 1 <= page_number <= len(source):
                raise ValueError("Docling получил несуществующую физическую страницу.")
            selected.insert_pdf(
                source, from_page=page_number - 1, to_page=page_number - 1
            )
            selected_content = selected.tobytes()
        try:
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self.settings.base_url.rstrip("/"),
                    headers={"X-Api-Key": self.settings.api_key.get_secret_value()},
                    timeout=self.settings.request_timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.post(
                        "/v1/convert/file/async",
                        files={
                            "files": ("page.pdf", selected_content, "application/pdf")
                        },
                        data={
                            "to_formats": "json",
                            "target_type": "inbody",
                            "do_ocr": str(do_ocr).lower(),
                            "force_ocr": "false",
                            "ocr_preset": self.settings.ocr_preset,
                            "ocr_lang": list(self.settings.ocr_languages),
                            "include_images": "false",
                            "include_page_images": "false",
                            "document_timeout": str(
                                self.settings.total_timeout_seconds
                            ),
                            "do_table_structure": "true",
                        },
                    )
                    response.raise_for_status()
                    state = response.json()
                    task_id = state["task_id"]
                    if (
                        not isinstance(task_id, str)
                        or not task_id
                        or any(
                            c
                            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                            for c in task_id
                        )
                    ):
                        raise DocumentConversionError(
                            "Неверный идентификатор задачи Docling."
                        )
                    while state["task_status"] in {"pending", "started"}:
                        await asyncio.sleep(self.settings.poll_interval_seconds)
                        state = (
                            await self._read(client, f"/v1/status/poll/{task_id}")
                        ).json()
                    if state["task_status"] != "success":
                        raise DocumentConversionError(
                            "Docling не завершил конверсию выбранной страницы."
                        )
                    result = (await self._read(client, f"/v1/result/{task_id}")).json()
                    if result.get("status") != "success" or not isinstance(
                        result.get("document", {}).get("json_content"), dict
                    ):
                        raise DocumentConversionError(
                            "Docling вернул неполный результат вместо полного документа."
                        )
                    value = {
                        "artifact_key": key,
                        "physical_page": page_number,
                        "converted_page": 1,
                        "task_id": task_id,
                        "request": identity,
                        "result": result,
                    }
                    await self.artifacts.save(key, value)
                    return value
        except (httpx.HTTPError, KeyError, ValueError, TimeoutError) as error:
            raise DocumentConversionError(
                "Выборочное чтение Docling недоступно."
            ) from error

    async def _read(self, client, path):
        """Ограниченно повторяет чтение состояния, не запуская конверсию заново."""
        for attempt in range(3):
            try:
                response = await client.get(path)
                if response.status_code not in {429, 502, 503, 504} or attempt == 2:
                    response.raise_for_status()
                    return response
            except (httpx.ConnectError, httpx.ReadError):
                if attempt == 2:
                    raise
            await asyncio.sleep(0.25 * 2**attempt)
        raise AssertionError("Недостижимое состояние повторов Docling.")
