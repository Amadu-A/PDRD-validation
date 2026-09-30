# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/experience_versions.py

"""HTTP-адаптер очереди версий с проверкой состава, модели и имени коллекции."""

import hashlib
import json
from uuid import UUID

from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.ports.experience_versions import (
    AppliedExperienceVersion,
    VersionIndexJob,
)
from pdrd_knowledge_service.domain.experience_admission import require_version_quality
from pdrd_knowledge_service.infrastructure.experience_feed import (
    HttpExperienceFeed,
    parse_example,
)


class HttpExperienceVersionQueue:
    """Использует тот же закрытый индексный канал, не передаёт Review actor."""

    def __init__(self, source: HttpExperienceFeed) -> None:
        """HTTP client внедряется из composition root, подходит для ASGI теста."""
        self.source = source

    async def claim(
        self, *, worker: str, model: str, identity: str, dimension: int
    ) -> VersionIndexJob | None:
        """Проверяет fingerprint каждого члена и общий manifest до GPU вызова."""
        response = await self.source._request(
            "POST",
            "/versions/claim",
            json={
                "worker": worker,
                "model": model,
                "identity": identity,
                "dimension": dimension,
            },
        )
        try:
            item = response.json()["item"]
            if item is None:
                return None
            version_id = UUID(item["id"])
            expected_collection = f"pdrd_e_{identity}_{version_id.hex}"
            body = {
                "schema_version": 1,
                "kind": item["kind"],
                "model": item["model"],
                "section_id": item["section_id"],
                "members": item["members"],
            }
            digest = hashlib.sha256(
                json.dumps(
                    body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if (
                item["kind"] != "vector"
                or item["status"] != "building"
                or item["model"] != model
                or item["embedding_identity"] != identity
                or item["dimension"] != dimension
                or item["collection"] != expected_collection
                or item["manifest_sha256"] != digest
                or not 1 <= len(item["members"]) <= 1000
                or not item["section_id"]
            ):
                raise ValueError("Несовместимое задание.")
            members = tuple(parse_example(raw) for raw in item["members"])
            if len({member.reference.example_id for member in members}) != len(
                members
            ) or any(
                member.data.get("section_id") != item["section_id"]
                for member in members
            ):
                raise ValueError("Несогласованный состав раздела.")
            return VersionIndexJob(
                version_id,
                expected_collection,
                identity,
                dimension,
                item["section_id"],
                members,
                digest,
            )
        except (ValueError, TypeError, KeyError) as error:
            raise ExperienceFeedError(
                "Реестр версий вернул некорректное задание."
            ) from error

    async def read(
        self, *, version_id: UUID, model: str, identity: str, dimension: int
    ) -> VersionIndexJob:
        """Проверочный поиск требует готовой совместимой версии и её фиксированного состава."""
        response = await self.source._request("GET", f"/versions/{version_id}")
        try:
            item = response.json()
            return _ready_job(
                item,
                version_id=version_id,
                model=model,
                identity=identity,
                dimension=dimension,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExperienceFeedError(
                "Нужна готовая векторная версия совместимой модели."
            ) from error

    async def applied(
        self,
        *,
        section_id: str,
        model: str,
        identity: str,
        dimension: int,
        top_k: int,
        min_score: float,
    ) -> AppliedExperienceVersion | None:
        """Получает действующее назначение и перепроверяет независимый допуск версии."""
        response = await self.source._request(
            "GET", "/applied", params={"section_id": section_id}
        )
        try:
            body = response.json()
            if not isinstance(body, dict) or set(body) != {"item"}:
                raise ValueError("Некорректный ответ назначения E.")
            item = body["item"]
            if item is None:
                return None
            job = _ready_job(
                item,
                version_id=UUID(item["id"]),
                model=model,
                identity=identity,
                dimension=dimension,
            )
            if (
                item["quality_approved"] is not True
                or item["section_id"] != section_id
                or not isinstance(item["quality_report"], dict)
            ):
                raise ValueError("Раздел либо допуск назначения не согласован.")
            report = item["quality_report"]
            require_version_quality(
                report,
                version_id=str(job.id),
                model=model,
                manifest_sha256=job.manifest_sha256,
                section_id=section_id,
                identity=identity,
                dimension=dimension,
                collection=job.collection,
                top_k=top_k,
                min_score=min_score,
                source_hashes={member.data["source_sha256"] for member in job.members},
            )
            digest = hashlib.sha256(
                json.dumps(
                    report,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            return AppliedExperienceVersion(job, digest)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ExperienceFeedError(
                "Применённая версия E несовместима либо её оценка не подтверждена."
            ) from error

    async def finish(
        self, *, job_id: UUID, worker: str, status: str, error: str = ""
    ) -> None:
        """Heartbeat относится только к своей аренде."""
        await self.source._request(
            "POST",
            f"/versions/{job_id}/result",
            json={
                "worker": worker,
                "status": status,
                "error": error,
            },
        )


def _ready_job(
    item: dict, *, version_id: UUID, model: str, identity: str, dimension: int
) -> VersionIndexJob:
    """Единый контракт готового индекса для операторского CLI и рабочего назначения."""
    body = {
        "schema_version": 1,
        "kind": item["kind"],
        "model": item["model"],
        "section_id": item["section_id"],
        "members": item["members"],
    }
    digest = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    expected = f"pdrd_e_{identity}_{version_id.hex}"
    if (
        item["id"] != str(version_id)
        or item["kind"] != "vector"
        or item["status"] != "ready"
        or item.get("deleted", False) is not False
        or item["model"] != model
        or item["embedding_identity"] != identity
        or type(item["dimension"]) is not int
        or item["dimension"] != dimension
        or item["collection"] != expected
        or item["manifest_sha256"] != digest
        or not isinstance(item["section_id"], str)
        or not 1 <= len(item["section_id"]) <= 200
    ):
        raise ValueError("Версия не готова или несовместима.")
    members = tuple(parse_example(raw) for raw in item["members"])
    if (
        not 1 <= len(members) <= 1000
        or len({member.reference.example_id for member in members}) != len(members)
        or any(
            member.data.get("section_id") != item["section_id"] for member in members
        )
    ):
        raise ValueError("Некорректный scope версии.")
    return VersionIndexJob(
        version_id, expected, identity, dimension, item["section_id"], members, digest
    )
