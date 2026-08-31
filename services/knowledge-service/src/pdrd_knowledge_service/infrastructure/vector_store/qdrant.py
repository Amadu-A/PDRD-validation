# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/vector_store/qdrant.py

"""Qdrant vector storage adapter."""

from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStoreError,
)
from pdrd_knowledge_service.domain.search import VectorPoint


class QdrantVectorStore:
    """Выполняет vector search через Qdrant REST API."""

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
        """Ищет ближайшие Qdrant points."""
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/collections/{collection}/points/query"),
                    json={
                        "query": vector,
                        "limit": limit,
                        "with_payload": True,
                        "with_vector": False,
                    },
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

    async def is_ready(self) -> bool:
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
        """Проверяет существование Qdrant collection."""
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
