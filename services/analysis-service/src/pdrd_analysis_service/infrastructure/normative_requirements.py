# services/analysis-service/src/pdrd_analysis_service/infrastructure/normative_requirements.py

"""Полная пагинация Knowledge с контролем scope, версии, полноты и отмены."""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import httpx

from pdrd_analysis_service.application.ports.normative_requirements import (
    AtomicNormativeDocument,
    NormativeRequirementsUnavailable,
)


@dataclass(frozen=True, slots=True)
class HttpNormativeRequirementsReader:
    """Использует существующий Knowledge API, не изменяя каталог или shared runtime."""

    base_url: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None

    async def load(
        self,
        *,
        section_id: str,
        document_id: str,
        generation_id: str | None,
        assessed_on: str,
        check_cancellation: Callable[[], Awaitable[None]],
    ) -> AtomicNormativeDocument:
        """Не возвращает частичный корпус как полный при обрыве или смене поколения."""
        rows, seen = [], set()
        offset, manifest = 0, None
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                await check_cancellation()
                response = await client.get(
                    f"/internal/v1/normative/sections/{section_id}/documents"
                )
                response.raise_for_status()
                documents = response.json()
                if not isinstance(documents, list) or not any(
                    str(item.get("document_id")) == document_id
                    and str(item.get("section_id")) == section_id
                    for item in documents
                    if isinstance(item, dict)
                ):
                    raise NormativeRequirementsUnavailable(
                        "document_outside_selected_section"
                    )
                while True:
                    await check_cancellation()
                    params = {
                        "offset": offset,
                        "limit": 200,
                        "assessed_on": assessed_on,
                    }
                    if generation_id:
                        params["generation_id"] = generation_id
                    response = await client.get(
                        f"/internal/v1/normative/documents/{document_id}/requirements",
                        params=params,
                    )
                    response.raise_for_status()
                    data = response.json()
                    if (
                        not isinstance(data, dict)
                        or data.get("document_id") != document_id
                        or data.get("state") != "ready"
                    ):
                        raise NormativeRequirementsUnavailable(
                            "invalid_atomic_generation"
                        )
                    try:
                        UUID(data.get("generation_id", ""))
                    except (ValueError, TypeError, AttributeError) as error:
                        raise NormativeRequirementsUnavailable(
                            "invalid_atomic_generation_id"
                        ) from error
                    if (
                        not isinstance(data.get("source_sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", data["source_sha256"])
                        or not isinstance(data.get("extractor_version"), str)
                        or not data["extractor_version"]
                        or not isinstance(data.get("status_evidence"), list)
                        or type(data.get("count")) is not int
                        or data["count"] < 0
                    ):
                        raise NormativeRequirementsUnavailable(
                            "invalid_atomic_manifest"
                        )
                    identity = {
                        key: data.get(key)
                        for key in (
                            "generation_id",
                            "document_id",
                            "source_sha256",
                            "extractor_version",
                            "count",
                            "status_evidence",
                        )
                    }
                    if (manifest is not None and identity != manifest) or (
                        generation_id and data.get("generation_id") != generation_id
                    ):
                        raise NormativeRequirementsUnavailable(
                            "atomic_generation_changed"
                        )
                    manifest = identity
                    generation_id = data["generation_id"]
                    batch = data.get("requirements")
                    if (
                        not isinstance(batch, list)
                        or type(data.get("offset")) is not int
                        or data["offset"] != offset
                        or offset + len(batch) > data["count"]
                    ):
                        raise NormativeRequirementsUnavailable(
                            "invalid_atomic_pagination"
                        )
                    for row in batch:
                        if (
                            not isinstance(row, dict)
                            or not all(
                                isinstance(row.get(key), str) and row[key]
                                for key in (
                                    "requirement_id",
                                    "point_id",
                                    "text",
                                    "source_text",
                                    "source_file",
                                )
                            )
                            or not all(
                                isinstance(row.get(key), str)
                                for key in (
                                    "clause",
                                    "subclause",
                                    "edition",
                                    "document_code",
                                    "object_type",
                                )
                            )
                            or not all(
                                isinstance(row.get(key), list)
                                and all(isinstance(value, str) for value in row[key])
                                for key in (
                                    "applicability_conditions",
                                    "exceptions",
                                    "normative_dependencies",
                                    "limitations",
                                    "required_parameters",
                                )
                            )
                            or type(row.get("page")) is not int
                            or row["page"] < 1
                            or row["requirement_id"] in seen
                            or row.get("document_id") != document_id
                            or row.get("source_sha256") != manifest["source_sha256"]
                            or not isinstance(row.get("normative_status"), dict)
                        ):
                            raise NormativeRequirementsUnavailable(
                                "ambiguous_atomic_requirement"
                            )
                        seen.add(row["requirement_id"])
                        rows.append(
                            {
                                **row,
                                "generation_id": generation_id,
                                "section_id": section_id,
                                "status_evidence": data.get("status_evidence", []),
                            }
                        )
                    next_offset = data.get("next_offset")
                    if next_offset is None:
                        break
                    if (
                        not batch
                        or type(next_offset) is not int
                        or next_offset != offset + len(batch)
                        or next_offset >= data["count"]
                    ):
                        raise NormativeRequirementsUnavailable(
                            "incomplete_atomic_pagination"
                        )
                    offset = next_offset
                if len(rows) != manifest["count"]:
                    raise NormativeRequirementsUnavailable(
                        "atomic_requirement_count_mismatch"
                    )
                return AtomicNormativeDocument(
                    tuple(rows), {**manifest, "assessed_on": assessed_on}
                )
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise NormativeRequirementsUnavailable(
                f"knowledge_atomic_read_failed:{type(error).__name__}"
            ) from error
