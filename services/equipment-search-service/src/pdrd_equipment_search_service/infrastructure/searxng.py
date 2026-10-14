# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/searxng.py

"""Адаптер локального JSON API SearXNG."""

from dataclasses import dataclass

import httpx

from pdrd_equipment_search_service.domain.equipment import SearchHit


@dataclass(slots=True)
class SearxngSearch:
    """Выполняет только ограниченные нейтральные запросы к локальному сервису."""

    client: httpx.AsyncClient
    base_url: str
    timeout_seconds: float = 8.0

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Возвращает ссылки без использования snippet как доказательства."""
        if not query.strip() or limit < 1:
            return ()
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
        return tuple(found)
