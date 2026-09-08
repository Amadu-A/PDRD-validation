# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/vector_store/qdrant.py

"""Qdrant vector storage adapter."""

from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.vector_store import (
    StoredVectorPayload,
    VectorStoreError,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
    VectorSearchFilter,
)


class QdrantVectorStore:
    """Vector operations через Qdrant REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Сохраняет HTTP settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = request_timeout_seconds

        self._health_timeout_seconds = health_timeout_seconds

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Ищет ближайшие points."""
        return await self._search(
            collection=collection,
            vector=vector,
            limit=limit,
            search_filter=None,
        )

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: VectorSearchFilter,
    ) -> list[VectorPoint]:
        """Ищет filtered points."""
        return await self._search(
            collection=collection,
            vector=vector,
            limit=limit,
            search_filter=search_filter,
        )

    async def _search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: VectorSearchFilter | None,
    ) -> list[VectorPoint]:
        """Выполняет Query Points."""
        payload: dict[str, Any] = {
            "query": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }

        if search_filter is not None:
            payload["filter"] = self._build_search_filter(
                search_filter,
            )

        response = await self._request(
            "POST",
            (f"/collections/{collection}/points/query"),
            json=payload,
        )

        points = (
            response.json()
            .get(
                "result",
                {},
            )
            .get(
                "points",
                [],
            )
        )

        if not isinstance(
            points,
            list,
        ):
            raise VectorStoreError(
                "Qdrant вернул invalid points.",
            )

        return [
            self._build_point(
                point,
            )
            for point in points
            if isinstance(
                point,
                dict,
            )
        ]

    @staticmethod
    def _build_search_filter(
        search_filter: VectorSearchFilter,
    ) -> dict[str, Any]:
        """Строит Qdrant filter."""
        conditions: list[dict[str, Any]] = []

        for condition in search_filter.must:
            if (
                len(
                    condition.values,
                )
                == 1
            ):
                match: dict[str, Any] = {
                    "value": condition.values[0],
                }

            else:
                match = {
                    "any": list(
                        condition.values,
                    ),
                }

            conditions.append(
                {
                    "key": condition.key,
                    "match": match,
                }
            )

        return {
            "must": conditions,
        }

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт Cosine collection."""
        await self._request(
            "PUT",
            f"/collections/{collection}",
            json={
                "vectors": {
                    "size": vector_size,
                    "distance": "Cosine",
                }
            },
        )

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет points."""
        if not records:
            return

        await self._request(
            "PUT",
            (f"/collections/{collection}/points"),
            params={
                "wait": "true",
            },
            json={
                "points": [
                    {
                        "id": record.point_id,
                        "vector": record.vector,
                        "payload": record.payload,
                    }
                    for record in records
                ]
            },
        )

    async def set_payload_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        payload: dict[str, Any],
    ) -> None:
        """Изменяет payload."""
        await self._request(
            "POST",
            (f"/collections/{collection}/points/payload"),
            params={
                "wait": "true",
            },
            json={
                "payload": payload,
                "filter": {
                    "must": [
                        {
                            "key": key,
                            "match": {
                                "value": value,
                            },
                        }
                    ]
                },
            },
        )

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Удаляет filtered points."""
        await self._request(
            "POST",
            (f"/collections/{collection}/points/delete"),
            params={
                "wait": "true",
            },
            json={
                "filter": {
                    "must": [
                        {
                            "key": key,
                            "match": {
                                "value": value,
                            },
                        }
                    ]
                }
            },
        )

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Идемпотентно удаляет collection."""
        response = await self._request(
            "DELETE",
            f"/collections/{collection}",
            allow_not_found=True,
        )

        return response.status_code != 404

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет readiness."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/readyz",
                )

            return response.is_success

        except httpx.HTTPError:
            return False

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет collection либо alias."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/collections/{collection}"
                )

        except httpx.HTTPError:
            return False

        if response.status_code == 404:
            return False

        return response.is_success

    async def list_collections(
        self,
    ) -> tuple[str, ...]:
        """Возвращает physical collections."""
        response = await self._request(
            "GET",
            "/collections",
        )

        collections = (
            response.json()
            .get(
                "result",
                {},
            )
            .get(
                "collections",
                [],
            )
        )

        if not isinstance(
            collections,
            list,
        ):
            raise VectorStoreError(
                "Qdrant вернул invalid collections list.",
            )

        return tuple(
            str(
                item.get(
                    "name",
                    "",
                )
            )
            for item in collections
            if isinstance(
                item,
                dict,
            )
            and item.get(
                "name",
            )
        )

    async def get_alias_target(
        self,
        alias: str,
    ) -> str | None:
        """Возвращает target global Qdrant alias."""
        response = await self._request(
            "GET",
            "/aliases",
        )

        aliases = (
            response.json()
            .get(
                "result",
                {},
            )
            .get(
                "aliases",
                [],
            )
        )

        if not isinstance(
            aliases,
            list,
        ):
            raise VectorStoreError(
                "Qdrant вернул invalid aliases list.",
            )

        for item in aliases:
            if not isinstance(
                item,
                dict,
            ):
                continue

            if (
                str(
                    item.get(
                        "alias_name",
                        "",
                    )
                )
                != alias
            ):
                continue

            collection_name = item.get(
                "collection_name",
            )

            if collection_name is None:
                return None

            return str(
                collection_name,
            )

        return None

    async def replace_aliases(
        self,
        aliases_to_targets: dict[str, str],
    ) -> None:
        """Atomically переключает несколько aliases."""
        actions: list[dict[str, Any]] = []

        for alias, target in aliases_to_targets.items():
            current = await self.get_alias_target(
                alias,
            )

            if current == target:
                continue

            if current is not None:
                actions.append(
                    {
                        "delete_alias": {
                            "alias_name": alias,
                        }
                    }
                )

            actions.append(
                {
                    "create_alias": {
                        "collection_name": target,
                        "alias_name": alias,
                    }
                }
            )

        if not actions:
            return

        await self._request(
            "POST",
            "/collections/aliases",
            json={
                "actions": actions,
            },
        )

    async def scroll_payloads(
        self,
        *,
        collection: str,
        batch_size: int = 256,
    ) -> tuple[StoredVectorPayload, ...]:
        """Читает все payloads collection."""
        result: list[StoredVectorPayload] = []

        offset: object | None = None

        while True:
            body: dict[str, Any] = {
                "limit": batch_size,
                "with_payload": True,
                "with_vector": False,
            }

            if offset is not None:
                body["offset"] = offset

            response = await self._request(
                "POST",
                (f"/collections/{collection}/points/scroll"),
                json=body,
            )

            payload = response.json().get(
                "result",
                {},
            )

            if not isinstance(
                payload,
                dict,
            ):
                raise VectorStoreError(
                    "Qdrant вернул invalid scroll result.",
                )

            points = payload.get(
                "points",
                [],
            )

            if not isinstance(
                points,
                list,
            ):
                raise VectorStoreError(
                    "Qdrant вернул invalid scroll points.",
                )

            for point in points:
                if not isinstance(
                    point,
                    dict,
                ):
                    continue

                point_payload = point.get(
                    "payload",
                    {},
                )

                if not isinstance(
                    point_payload,
                    dict,
                ):
                    point_payload = {}

                result.append(
                    StoredVectorPayload(
                        point_id=str(
                            point.get(
                                "id",
                                "",
                            )
                        ),
                        payload=dict(
                            point_payload,
                        ),
                    )
                )

            offset = payload.get(
                "next_page_offset",
            )

            if offset is None:
                break

        return tuple(
            result,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        """Выполняет общий Qdrant request."""
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json,
                    params=params,
                )

        except httpx.HTTPError as error:
            raise VectorStoreError(
                f"Не удалось обратиться к Qdrant: {error}",
            ) from error

        if allow_not_found and response.status_code == 404:
            return response

        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise VectorStoreError(
                f"Qdrant вернул ошибку: {response.status_code}: {response.text[:1000]}",
            ) from error

        return response

    @staticmethod
    def _build_point(
        point: dict[str, Any],
    ) -> VectorPoint:
        """Преобразует Qdrant point."""
        payload = point.get(
            "payload",
            {},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        return VectorPoint(
            point_id=str(
                point.get(
                    "id",
                    "",
                )
            ),
            score=float(
                point.get(
                    "score",
                    0.0,
                )
                or 0.0
            ),
            payload=payload,
        )
