# services/document-service/src/pdrd_document_service/infrastructure/specification_extraction.py

"""Клиент опубликованного REST API Spec Extracting AI, без доступа к его БД."""

import asyncio
import hashlib
import json
from contextlib import suppress
from uuid import UUID, uuid4

import httpx

from pdrd_document_service.application.ports.specification_extraction import (
    ExtractionArtifactStore,
    SpecificationExtraction,
    SpecificationExtractionError,
)


class HttpSpecificationExtractor:
    """Получает полный JSON и warnings, сохраняя их в PDRD до истечения внешнего TTL."""

    def __init__(
        self, *, settings, artifacts: ExtractionArtifactStore, transport=None
    ) -> None:
        """Сохраняет выделенный ключ, ограничения и собственное хранилище артефактов."""
        self.settings, self.artifacts, self.transport = settings, artifacts, transport

    async def extract(
        self,
        *,
        content: bytes,
        file_name: str,
        pages: tuple[int, ...],
        project_code: str,
    ) -> SpecificationExtraction:
        """Создаёт идемпотентное задание и ограниченно ожидает терминальное состояние."""
        if not pages or any(isinstance(p, bool) or p < 1 for p in pages):
            raise ValueError("Не заданы физические страницы спецификации.")
        identity = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "pages": sorted(set(pages)),
            "project_code": project_code,
            "version": self.settings.cache_version,
            "provider": self.settings.base_url,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cached = await self.artifacts.load(key)
        if cached is not None:
            return SpecificationExtraction(
                cached["job_id"],
                cached["result"],
                tuple(cached["warnings"]),
                cached["status"],
                key,
            )
        job_id = None
        # Ключ операции живёт весь цикл транспортных повторов. Новая попытка
        # после отмены/ошибки получает новый ключ; готовый результат по-прежнему
        # переиспользуется по детерминированной идентичности содержимого.
        operation_key = hashlib.sha256(f"{key}:{uuid4()}".encode()).hexdigest()
        async with httpx.AsyncClient(
            base_url=self.settings.base_url.rstrip("/"),
            headers={"X-API-Key": self.settings.api_key.get_secret_value()},
            timeout=self.settings.request_timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            try:
                async with asyncio.timeout(self.settings.total_timeout_seconds):
                    response = await self._request(
                        client,
                        "POST",
                        "/api/v1/extractions",
                        headers={"Idempotency-Key": operation_key},
                        files={"file": (file_name, content, "application/pdf")},
                        data={
                            "pages": ",".join(str(p) for p in sorted(set(pages))),
                            "project_code": project_code,
                        },
                    )
                    state = response.json()
                    job_id = str(UUID(state["job_id"]))
                    while state["status"] in {
                        "queued",
                        "running",
                        "pending",
                        "cancelling",
                    }:
                        await asyncio.sleep(self.settings.poll_interval_seconds)
                        state = (
                            await self._request(
                                client, "GET", f"/api/v1/extractions/{job_id}"
                            )
                        ).json()
                    if state["status"] not in {"completed", "completed_with_warnings"}:
                        raise SpecificationExtractionError(
                            "Извлечение спецификации не завершено: " + state["status"]
                        )
                    if sorted(state["selected_pdf_pages"]) != sorted(
                        set(pages)
                    ) or state["processed_pages"] != len(set(pages)):
                        raise SpecificationExtractionError(
                            "Внешнее извлечение не сохранило все выбранные страницы."
                        )
                    result = (
                        await self._request(
                            client, "GET", f"/api/v1/extractions/{job_id}/result.json"
                        )
                    ).json()
                    self._validate(result, pages)
                    if state["item_count"] != len(result["items"]):
                        raise SpecificationExtractionError(
                            "Количество строк не совпадает со статусом внешнего задания."
                        )
                    warnings, offset = [], 0
                    while True:
                        batch = (
                            await self._request(
                                client,
                                "GET",
                                f"/api/v1/extractions/{job_id}/warnings",
                                params={"offset": offset, "limit": 200},
                            )
                        ).json()
                        warnings.extend(batch["warnings"])
                        offset = len(warnings)
                        if offset >= batch["total"]:
                            break
                        if not batch["warnings"]:
                            raise SpecificationExtractionError(
                                "Получена неполная диагностика спецификации."
                            )
                    value = {
                        "job_id": job_id,
                        "status": state["status"],
                        "result": result,
                        "warnings": warnings,
                        "request": identity,
                        "prompt_version": state.get("prompt_version"),
                    }
                    await self.artifacts.save(key, value)
                    return SpecificationExtraction(
                        job_id, result, tuple(warnings), state["status"], key
                    )
            except (TimeoutError, asyncio.CancelledError):
                if job_id:
                    with suppress(httpx.HTTPError):
                        await asyncio.shield(
                            client.post(
                                f"/api/v1/extractions/{job_id}/cancel", timeout=5
                            )
                        )
                raise
            except (httpx.HTTPError, KeyError, ValueError) as error:
                raise SpecificationExtractionError(
                    "Не удалось получить полный результат Spec Extracting AI."
                ) from error

    async def _request(self, client, method, path, **kwargs):
        """Повторяет только временный HTTP-сбой с тем же Idempotency-Key и общим сроком."""
        for attempt in range(3):
            try:
                response = await client.request(method, path, **kwargs)
                if response.status_code not in {429, 502, 503, 504} or attempt == 2:
                    response.raise_for_status()
                    return response
                delay = response.headers.get("Retry-After", "")
                try:
                    seconds = max(0, min(float(delay), 10))
                except ValueError:
                    seconds = 0.25 * 2**attempt
            except (httpx.ConnectError, httpx.ReadError):
                if attempt == 2:
                    raise
                seconds = 0.25 * 2**attempt
            await asyncio.sleep(seconds)
        raise AssertionError("Недостижимое состояние повторов.")

    @staticmethod
    def _validate(result, pages):
        """Не нормализует оригинальные поля и не придумывает Brand/Article."""
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise SpecificationExtractionError("Неверный контракт JSON спецификации.")
        for item in result["items"]:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str)
                for k in (
                    "Caption",
                    "Units",
                    "Quantity",
                    "UserComment1",
                    "Pages",
                    "Brand",
                    "Article",
                    "Note",
                )
            ):
                raise SpecificationExtractionError(
                    "Потеря исходных строковых полей спецификации."
                )
            if not item["Pages"].isdigit() or int(item["Pages"]) not in pages:
                raise SpecificationExtractionError(
                    "Строка относится к невыбранной физической странице."
                )
