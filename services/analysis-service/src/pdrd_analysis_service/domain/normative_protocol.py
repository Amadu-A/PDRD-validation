# services/analysis-service/src/pdrd_analysis_service/domain/normative_protocol.py

"""Канонический lossless-протокол и компактная проекция транспортных метрик."""

import hashlib
import json
from collections import Counter


def canonical_json(value):
    """Задаёт один UTF-8 формат для digest, повторной публикации и проверки байтов."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def protocol_archive(
    *,
    document_id,
    section_id,
    document_ids,
    diagnostics,
    sources,
    page_metrics=(),
    stage_records=(),
):
    """Хранит одинаковые значения один раз; идентичность документа/редакции не склеивается."""
    nodes = {}

    def encode(value):
        """Типизированные узлы исключают конфликт ссылок с исходными ключами JSON."""
        if isinstance(value, dict):
            kind, payload = (
                "object",
                {key: encode(child) for key, child in value.items()},
            )
        elif isinstance(value, (list, tuple)):
            kind, payload = "array", [encode(child) for child in value]
        elif isinstance(value, str) and len(value) > 160:
            kind, payload = "text", value
        else:
            return value
        node = {"kind": kind, "value": payload}
        key = hashlib.sha256(canonical_json(node)).hexdigest()
        nodes.setdefault(key, {"id": key, **node})
        return {"$ref": key}

    roots = {
        "diagnostics": encode(diagnostics),
        "sources": encode(sources),
        "checks": encode(diagnostics.get("checks", [])),
        "selection": encode(diagnostics.get("selection", [])),
        "page_metrics": encode(page_metrics),
        "stage_records": encode(stage_records),
    }
    return {
        "schema": "pdrd.normative-protocol/v1",
        "document_id": document_id,
        "section_id": section_id,
        "document_ids": sorted(document_ids),
        "roots": roots,
        "nodes": list(nodes.values()),
        "counts": {
            "checks": len(diagnostics.get("checks", [])),
            "selection": len(diagnostics.get("selection", [])),
            "sources": len(sources),
            "stage_records": len(stage_records),
        },
    }


def transport_summary(diagnostics):
    """Передаёт вычисленные счётчики; ни checks, ни цитаты не становятся HTTP metrics."""
    fields = (
        "reason_code",
        "result",
        "applicability",
        "currency",
        "rule",
        "source_kind",
    )
    checks = diagnostics.get("checks", [])
    summary = {
        key: value
        for key, value in diagnostics.items()
        if key not in {"checks", "selection", "generations", "legacy_records"}
    }
    summary["generations"] = [
        {
            key: row.get(key)
            for key in (
                "document_id",
                "generation_id",
                "source_sha256",
                "extractor_version",
                "count",
            )
        }
        for row in diagnostics.get("generations", [])
    ]
    summary["check_count"] = len(checks)
    summary["unique_check_requirements"] = len(
        {row["requirement_id"] for row in checks}
    )
    summary["check_counts"] = {
        field: dict(Counter(row.get(field, "unknown") for row in checks))
        for field in fields
    }
    summary["candidate_requirement_ids"] = sorted(
        {
            row["requirement_id"]
            for row in checks
            if row.get("result") in {"violated", "question"}
        }
    )
    return summary
