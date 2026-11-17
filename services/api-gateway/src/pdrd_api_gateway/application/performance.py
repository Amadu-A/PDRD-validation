# services/api-gateway/src/pdrd_api_gateway/application/performance.py

"""Сводка измеренных этапов по сохранённым метрикам собственного задания Gateway."""

from datetime import datetime


def performance_report(*, job, result: dict) -> dict | None:
    """Связывает document_id измерителя с реальным job_id и не суммирует параллельный wall time."""
    profiles, invalid = [], 0
    for page in result.get("pages", []):
        for name in (
            "understanding",
            "normative_check",
            "technical_assignment_first_pass",
            "finalization",
        ):
            section = page.get(name) or {}
            profile = (section.get("metrics") or {}).get("execution_profile")
            if profile is None:
                continue
            if not isinstance(profile, dict) or profile.get("document_id") != str(
                job.document_id
            ):
                invalid += 1
                continue
            profiles.append(profile)
    if not profiles:
        return None
    stages = []
    for name in dict.fromkeys(p["stage"] for p in profiles):
        rows = [p for p in profiles if p["stage"] == name]
        started = [datetime.fromisoformat(p["started_at"]) for p in rows]
        ended = [datetime.fromisoformat(p["ended_at"]) for p in rows]
        totals = {}
        for key in (
            "logical_vlm_calls",
            "physical_vlm_calls",
            "retries",
            "provider_work_ms",
            "input_tokens",
            "output_tokens",
            "gpu_wait_ms",
            "cache_hits",
            "cache_misses",
            "context_bytes",
            "image_bytes",
        ):
            values = [p.get(key) for p in rows]
            totals[key] = (
                None if any(value is None for value in values) else sum(values)
            )
        stages.append(
            {
                "name": name,
                "started_at": min(started).isoformat(),
                "ended_at": max(ended).isoformat(),
                "wall_ms": round((max(ended) - min(started)).total_seconds() * 1000, 2),
                "pages": len({p["page"] for p in rows}),
                **totals,
            }
        )
    return {
        "schema": "pdrd.performance.v1",
        "job_id": str(job.id),
        "document_id": str(job.document_id),
        "total_wall_ms": None
        if job.source_artifacts_deleted_at is not None
        else round((job.updated_at - job.created_at).total_seconds() * 1000, 2),
        "stages": stages,
        "rejected_profiles": invalid,
        "limitations": [
            "total_wall_ms включает ожидание задания; этапы содержат только измеренные операции.",
            "provider duration содержит работу HTTP-провайдера и его очередь; GPU wait — очередь PDRD.",
            "Неизмеренные операции и неизвестные токены не подменяются нулём.",
        ],
    }
