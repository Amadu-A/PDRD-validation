# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/vector_store/qdrant.py

"""Qdrant vector storage adapter."""

from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.vector_store import (
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
    """Выполняет vector operations через Qdrant REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Сохраняет параметры Qdrant adapter."""
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
        """Ищет ближайшие Qdrant points без payload filter."""
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
        """Ищет Qdrant points только внутри указанного payload scope."""
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
        """Выполняет общий Qdrant Query Points request."""
        payload: dict[
            str,
            Any,
        ] = {
            "query": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }

        if search_filter is not None:
            payload["filter"] = self._build_search_filter(
                search_filter,
            )

        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/collections/{collection}/points/query",
                    json=payload,
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise VectorStoreError(
                f"Qdrant collection {collection} "
                "вернула ошибку: "
                f"{error.response.status_code}: "
                f"{error.response.text[:1000]}",
            ) from error

        except httpx.HTTPError as error:
            raise VectorStoreError(
                f"Не удалось обратиться к Qdrant collection {collection}: {error}",
            ) from error

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
                "Qdrant вернул некорректный формат points.",
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
        """Преобразует generic Domain filter в Qdrant payload filter."""
        conditions: list[dict[str, Any]] = []

        for condition in search_filter.must:
            if (
                len(
                    condition.values,
                )
                == 1
            ):
                match: dict[
                    str,
                    Any,
                ] = {
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
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.put(
                    f"{self._base_url}/collections/{collection}",
                    json={
                        "vectors": {
                            "size": vector_size,
                            "distance": "Cosine",
                        }
                    },
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VectorStoreError(
                f"Не удалось создать Qdrant collection {collection}: {error}",
            ) from error

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет vector records."""
        if not records:
            return

        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.put(
                    f"{self._base_url}/collections/{collection}/points",
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

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VectorStoreError(
                "Не удалось сохранить points "
                f"в Qdrant collection {collection}: "
                f"{error}",
            ) from error

    async def set_payload_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        payload: dict[
            str,
            Any,
        ],
    ) -> None:
        """Изменяет payload Qdrant points по точному match."""
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/collections/{collection}/points/payload",
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

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VectorStoreError(
                "Не удалось изменить payload points "
                f"в Qdrant collection {collection}: "
                f"{error}",
            ) from error

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Удаляет Qdrant points по точному payload match."""
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/collections/{collection}/points/delete",
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

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VectorStoreError(
                "Не удалось удалить filtered points "
                f"из Qdrant collection {collection}: "
                f"{error}",
            ) from error

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Идемпотентно удаляет Qdrant collection."""
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.delete(
                    f"{self._base_url}/collections/{collection}"
                )

        except httpx.HTTPError as error:
            raise VectorStoreError(
                f"Не удалось удалить Qdrant collection {collection}: {error}",
            ) from error

        if response.status_code == 404:
            return False

        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise VectorStoreError(
                "Qdrant вернул ошибку удаления "
                f"collection {collection}: "
                f"{response.status_code}: "
                f"{response.text[:1000]}",
            ) from error

        return True

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет readiness Qdrant."""
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
        """Проверяет существование vector collection."""
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

    @staticmethod
    def _build_point(
        point: dict[str, Any],
    ) -> VectorPoint:
        """Преобразует Qdrant point в Domain."""
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
