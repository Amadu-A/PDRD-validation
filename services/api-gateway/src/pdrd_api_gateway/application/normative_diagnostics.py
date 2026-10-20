# services/api-gateway/src/pdrd_api_gateway/application/normative_diagnostics.py

"""Компактная проекция уже сохранённых метрик без текстов документа и векторов."""

import hashlib
import json
from collections import Counter


def normative_diagnostics(result: dict, *, job_id: str) -> dict:
    """Сводит реальные проверки и операции по ID; отсутствие телеметрии не считает нулевой нагрузкой."""
    pages = result.get("pages") or (
        [result["page"]]
        if isinstance(result.get("page"), dict)
        else [result]
        if "page_number" in result
        else []
    )
    operations = {}
    records = {}
    legacy = {}
    rejections = Counter()
    plans = []

    def collect(value):
        """Читает только узлы метрик, не копируя содержимое проекта в сводку."""
        if isinstance(value, dict):
            metric = value.get("retrieval_metrics")
            if isinstance(metric, dict) and metric.get("operation_id"):
                operations[metric["operation_id"]] = metric
            plan = value.get("requirement_plan")
            if isinstance(plan, dict):
                plans.append(plan)
                for record in plan.get("checks", []):
                    if isinstance(record, dict) and record.get("requirement_id"):
                        # Одна норма на разных страницах может проверять разные значения.
                        evidence_key = hashlib.sha256(
                            json.dumps(
                                record.get("observations"), sort_keys=True
                            ).encode()
                        ).hexdigest()
                        records[(record["requirement_id"], evidence_key)] = record
                for record in plan.get("legacy_records", []):
                    if isinstance(record, dict) and record.get("check_id"):
                        legacy[record["check_id"]] = record
            for rejected in value.get("rejected_findings", []) or []:
                if isinstance(rejected, dict):
                    rejections[str(rejected.get("reason", "unknown"))] += 1
            for key, child in value.items():
                if key in {
                    "pages",
                    "page",
                    "metrics",
                    "normative_check",
                    "retrieval_operations",
                    "finalization",
                    "cross_page_consistency",
                }:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(result)
    counts = Counter()
    batches = Counter()
    failures = Counter()
    stages = {}
    for metric in operations.values():
        for key in (
            "logical_inputs",
            "unique_inputs",
            "physical_requests",
            "cache_hits",
            "http_429",
            "http_503",
            "timeouts",
            "retries",
            "wait_ms",
            "provider_ms",
            "input_characters",
            "prompt_tokens",
        ):
            counts[key] += metric.get(key, 0)
        batches.update(map(str, metric.get("batch_sizes", [])))
        failures.update(metric.get("failure_reasons", {}))
        stage = metric.get("stage", "unknown")
        stages.setdefault(stage, Counter())
        stages[stage].update(
            {
                key: metric.get(key, 0)
                for key in ("physical_requests", "retries", "wait_ms", "provider_ms")
            }
        )
    fields = {
        name: dict(Counter(record.get(name, "unknown") for record in records.values()))
        for name in (
            "reason_code",
            "result",
            "applicability",
            "currency",
            "rule",
            "source_kind",
        )
    }
    source_counts = {
        kind: sum(len(page.get(field) or []) for page in pages)
        for kind, field in (
            ("N", "normative_sources"),
            ("T", "technical_assignment_sources"),
            ("U", "user_package_sources"),
        )
    }
    checks = [
        record
        for record in records.values()
        if record.get("result") in {"violated", "question"}
    ]
    created_n = {
        record["requirement_id"]
        for record in checks
        if record.get("source_kind") == "N"
    }
    created_n.update(
        "legacy:" + record["check_id"]
        for record in legacy.values()
        if record.get("source_kind") == "N"
        and record.get("result") in {"confirmed", "needs_review"}
    )
    final = result.get("findings", [])
    retained_n = set()
    for finding in final:
        for origin in finding.get("origin_assertions", []):
            check = origin.get("check", {})
            requirement_id = check.get("calculation", {}).get("requirement_id")
            if finding.get("status") != "hypothesis" and requirement_id in created_n:
                retained_n.add(requirement_id)
            legacy_id = "legacy:" + str(check.get("check_id", ""))
            if finding.get("status") != "hypothesis" and legacy_id in created_n:
                retained_n.add(legacy_id)
    query_counts = Counter()
    for page in pages:
        query_counts.update(page.get("normative_query_counts") or {})
    if any("normative_queries" in page for page in pages):
        query_counts["unique_document_queries"] = len(
            {
                " ".join(query.split())
                for page in pages
                for query in page.get("normative_queries", [])
                if isinstance(query, str)
            }
        )
    return {
        "job_id": job_id,
        "stage": "normative_analysis",
        "pages": len(pages),
        "telemetry_available": bool(operations),
        "queries": dict(query_counts) if query_counts else None,
        "embedding": {
            **dict(counts),
            "batch_sizes": dict(batches),
            "failure_reasons": dict(failures),
        }
        if operations
        else None,
        "embedding_stages": {key: dict(value) for key, value in stages.items()},
        "source_counts": source_counts,
        "requirements": len(records),
        "unique_requirements": len(
            {record["requirement_id"] for record in records.values()}
        ),
        "requirement_plan_available": bool(plans),
        "requirement_plan": fields,
        "legacy_checks": {
            "count": len(legacy),
            "reason_codes": dict(
                Counter(r.get("reason_code", "unknown") for r in legacy.values())
            ),
        },
        "finalization_rejection_reasons": dict(rejections),
        "normative_candidates": {
            "created": len(created_n),
            "retained": len(retained_n),
            "not_retained": len(created_n - retained_n),
        },
        "limitations": [
            "Охват относится к найденным фрагментам, а не ко всему нормативному корпусу.",
            "Отсутствующие метрики старого прогона не восстановлены предположениями.",
        ],
    }
