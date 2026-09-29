# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/experience_feed.py

"""Закрытый HTTP-клиент E: ограниченный JSON, проверка версии и целостности crop.

Read-only ключ хранится на сервере и не совпадает с полномочиями Gateway Review.
Ошибки источника запрещают выдавать устаревшие локальные данные вместо проверки.
"""

import hashlib
import json
import re
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.domain.experience_index import (
    ExampleReference,
    TrustedExample,
)


def parse_example(raw: dict) -> TrustedExample:
    """Проверяет отпечаток канонического содержимого, типы и границы приватного JSON."""
    try:
        if not isinstance(raw, dict):
            raise ValueError("Проекция должна быть объектом.")
        reference = ExampleReference(
            UUID(raw["example_id"]), raw["example_revision"], raw["fingerprint"]
        )
        if (
            type(reference.example_revision) is not int
            or reference.example_revision < 0
        ):
            raise ValueError("Некорректная ревизия.")
        body = {key: value for key, value in raw.items() if key != "fingerprint"}
        digest = hashlib.sha256(
            json.dumps(
                body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if (
            digest != reference.fingerprint
            or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1
        ):
            raise ValueError("Неподдерживаемая версия проекции.")
        if raw["learning_use"] not in {"positive", "negative"} or raw[
            "decision"
        ] not in {"accepted", "rejected"}:
            raise ValueError("Неоднозначная разметка.")
        if raw["tag"] not in {"wise", "bad", "edited", "gold"}:
            raise ValueError("Неизвестный тег.")
        positive = raw["learning_use"] == "positive"
        if (
            raw["decision"] != ("accepted" if positive else "rejected")
            or (raw["tag"] in {"wise", "gold"} and not positive)
            or (raw["tag"] == "bad" and positive)
        ):
            raise ValueError("Решение не соответствует назначению примера.")
        if not isinstance(raw["texts"], list) or not isinstance(raw["crops"], list):
            raise ValueError("Формулировки и области должны быть массивами.")
        if not 1 <= len(raw["texts"]) <= 2 or not 1 <= len(raw["crops"]) <= 4:
            raise ValueError("Некорректный набор областей или формулировок.")
        for text in raw["texts"]:
            if (
                text["target"] not in {"original", "revised"}
                or not isinstance(text["text"], str)
                or not 1 <= len(text["text"].strip()) <= 10000
            ):
                raise ValueError("Некорректная формулировка.")
        targets = [item["target"] for item in raw["texts"]]
        if len(set(targets)) != len(targets):
            raise ValueError("Повтор объекта разметки.")
        expected_targets = {"revised"} if positive else {"original"}
        if not positive and raw["tag"] == "edited":
            if not raw["rejection_reason"].strip() or raw["negative_target"] not in {
                "original",
                "revised",
                "both",
            }:
                raise ValueError("Неоднозначный отрицательный пример.")
            expected_targets = (
                {"original", "revised"}
                if raw["negative_target"] == "both"
                else {raw["negative_target"]}
            )
        if set(targets) != expected_targets or any(
            item["text"]
            != raw["original_text" if item["target"] == "original" else "text"]
            for item in raw["texts"]
        ):
            raise ValueError("Формулировка не соответствует разметке.")
        for crop in raw["crops"]:
            if not re.fullmatch(r"[a-f0-9]{64}", crop["sha256"]) or any(
                type(crop[key]) is not int or not 1 <= crop[key] <= 10000
                for key in ("width", "height")
            ):
                raise ValueError("Некорректная область.")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", raw["source_sha256"])
            or type(raw["approved_revision"]) is not int
            or raw["approved_revision"] < 0
            or not isinstance(raw["source_filename"], str)
            or not 1 <= len(raw["source_filename"]) <= 500
        ):
            raise ValueError("Некорректное происхождение E.")
        UUID(raw["job_id"])
        UUID(raw["document_id"])
        if type(raw["page_number"]) is not int or raw["page_number"] < 1:
            raise ValueError("Некорректный лист.")
        for key in (
            "original_text",
            "text",
            "normative_basis",
            "normative_reference",
            "rejection_reason",
            "negative_target",
            "finding_id",
        ):
            if not isinstance(raw[key], str) or len(raw[key]) > 10000:
                raise ValueError("Некорректные метаданные E.")
        return TrustedExample(reference, raw)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ExperienceFeedError(
            "Experience вернул некорректную проекцию E."
        ) from error


class FeedPage(BaseModel):
    """Ограничивает размер страницы даже при ошибке удалённого сервиса."""

    model_config = ConfigDict(extra="forbid")
    items: list[dict] = Field(max_length=100)
    next_after: UUID | None = None


class HttpExperienceFeed:
    """Реализует порт страницы, проверки и изображения через отдельный ключ."""

    def __init__(
        self,
        base_url: str,
        key: str,
        *,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Сохраняет внедрённые HTTP-настройки, не открывая соединение при импорте."""
        self.base_url, self.key, self.timeout, self.transport = (
            base_url.rstrip("/"),
            key,
            timeout,
            transport,
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Сбой сети, ключа или HTTP исключает fallback на старые примеры."""
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.key}"},
            ) as client:
                response = await client.request(
                    method,
                    self.base_url + "/internal/v1/experience-index" + path,
                    **kwargs,
                )
                response.raise_for_status()
                if len(response.content) > 20_000_000:
                    raise ExperienceFeedError(
                        "Ответ источника E превышает допустимый размер."
                    )
                return response
        except httpx.HTTPError as error:
            raise ExperienceFeedError(
                "Доверенный источник E временно недоступен."
            ) from error

    async def page(
        self, *, after: UUID | None, limit: int
    ) -> tuple[tuple[TrustedExample, ...], UUID | None]:
        """Парсит страницу и передаёт курсор, даже если подходящих примеров нет."""
        response = await self._request(
            "GET",
            "/feed",
            params={"limit": limit, **({"after": str(after)} if after else {})},
        )
        try:
            page = FeedPage.model_validate(response.json())
            return tuple(parse_example(raw) for raw in page.items), page.next_after
        except (ValidationError, ValueError) as error:
            raise ExperienceFeedError("Некорректная страница источника E.") from error

    async def verify(
        self, references: tuple[ExampleReference, ...]
    ) -> tuple[TrustedExample, ...]:
        """Сверяет ссылки и не принимает незапрошенные либо дублированные редакции."""
        if not references:
            return ()
        response = await self._request(
            "POST",
            "/verify",
            json={"references": [item.as_dict() for item in references]},
        )
        try:
            raw = response.json()
            if set(raw) != {"items"} or len(raw["items"]) > len(references):
                raise ValueError("Некорректный ответ проверки E.")
            items = tuple(parse_example(item) for item in raw["items"])
            if len({item.reference for item in items}) != len(items) or any(
                item.reference not in references for item in items
            ):
                raise ValueError("Незапрошенная редакция E.")
            return items
        except (KeyError, TypeError, ValueError) as error:
            raise ExperienceFeedError("Некорректная проверка источника E.") from error

    async def crop(self, *, example: TrustedExample, index: int) -> bytes:
        """Проверяет PNG и SHA-256 перед отправкой изображения embedding-модели."""
        response = await self._request(
            "GET",
            f"/crops/{example.reference.example_id}/{index}",
            params={"fingerprint": example.reference.fingerprint},
        )
        content = response.content
        if (
            not content.startswith(b"\x89PNG\r\n\x1a\n")
            or hashlib.sha256(content).hexdigest()
            != example.data["crops"][index]["sha256"]
        ):
            raise ExperienceFeedError(
                "Область E не соответствует подтверждённому изображению."
            )
        return content
