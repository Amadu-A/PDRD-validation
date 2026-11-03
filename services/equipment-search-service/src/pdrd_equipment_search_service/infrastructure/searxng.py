# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/searxng.py

"""Адаптер общего JSON API SearXNG, которым владеет shared-infrastructure."""

import asyncio
import random
from dataclasses import dataclass

import httpx

from pdrd_equipment_search_service.domain.equipment import SearchHit


class SearchResults(tuple):
    """Сохраняет частичную выдачу вместе с диагностикой поисковых движков."""

    def __new__(cls, hits=(), *, engines=(), attempts=1):
        """Остаётся совместимым с прежним tuple-портом."""
        result = super().__new__(cls, hits)
        result.engines = engines
        result.attempts = attempts
        return result


class SearchUnavailable(ValueError):
    """Отличает недоступный поиск от успешного ответа без документов."""

    def __init__(self, code, engines=()):
        """Передаёт безопасные причины без исходного HTTP-ответа."""
        super().__init__("Поисковые движки общего поиска временно недоступны.")
        self.code = code
        self.engines = engines


@dataclass(slots=True)
class SearxngSearch:
    """Выполняет ограниченные нейтральные запросы к логическому shared endpoint."""

    client: httpx.AsyncClient
    base_url: str
    timeout_seconds: float = 8.0
    max_attempts: int = 2
    backoff_seconds: float = 0.5

    async def is_ready(self) -> bool:
        """Проверяет сетевую доступность shared без внешнего поискового запроса."""
        response = await self.client.get(
            self.base_url.rstrip("/") + "/", timeout=min(self.timeout_seconds, 3.0)
        )
        response.raise_for_status()
        return True

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Возвращает ссылки без использования snippet как доказательства."""
        if not query.strip() or limit < 1:
            return ()
        for attempt in range(self.max_attempts):
            try:
                return await self._search_once(query, limit, attempt + 1)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                failure = SearchUnavailable(
                    "search_timeout"
                    if isinstance(error, httpx.TimeoutException)
                    else "search_network_error"
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {429, 500, 502, 503, 504}:
                    raise SearchUnavailable("search_http_error") from error
                failure = SearchUnavailable(
                    "search_rate_limited"
                    if error.response.status_code == 429
                    else "search_http_error"
                )
            except SearchUnavailable as error:
                failure = error
            except (ValueError, TypeError) as error:
                raise SearchUnavailable("search_invalid_json") from error
            if attempt + 1 == self.max_attempts:
                raise failure
            await asyncio.sleep(
                min(8.0, self.backoff_seconds * 2**attempt)
                + random.uniform(0, self.backoff_seconds / 4)
            )
        raise SearchUnavailable("search_temporarily_unavailable")

    async def _search_once(
        self, query: str, limit: int, attempt: int
    ) -> tuple[SearchHit, ...]:
        """Не отбрасывает полезные ссылки при частичной недоступности движков."""
        response = await self.client.get(
            f"{self.base_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "language": "all"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Некорректный JSON ответ SearXNG.")
        rows = payload.get("results", [])
        if not isinstance(rows, list):
            raise ValueError("SearXNG вернул некорректный список результатов.")
        engines = tuple(
            {"engine": str(row[0])[:80], "reason": str(row[1])[:120]}
            for row in payload.get("unresponsive_engines", [])[:30]
            if isinstance(row, (list, tuple)) and len(row) >= 2
        )
        if not rows and engines:
            raise SearchUnavailable("search_engines_unavailable", engines)
        found: list[SearchHit] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str) or url in seen:
                continue
            hit = SearchHit(url, str(row.get("title") or "")[:200])
            if not hit.hostname:
                continue
            seen.add(url)
            found.append(hit)
            if len(found) >= limit:
                break
        return SearchResults(found, engines=engines, attempts=attempt)
