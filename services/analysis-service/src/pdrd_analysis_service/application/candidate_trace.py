# services/analysis-service/src/pdrd_analysis_service/application/candidate_trace.py

"""Связь пути кандидата с заданием без записи исходных документов и промптов в логи."""

import hashlib
import json
import logging

from pdrd_analysis_service.application.vision_execution import current_vision_execution

logger = logging.getLogger(__name__)


def candidate_key(candidate: dict, *, page: int | None = None) -> str:
    """Возвращает устойчивый идентификатор исходного утверждения, включая объект и геометрию."""
    content = {
        key: candidate.get(key)
        for key in ("comment", "evidence", "object_ref", "visual_regions")
    }
    content["physical_page"] = page
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def trace_candidate(
    stage: str,
    candidate_id: str,
    *,
    page: int | None = None,
    decision: str = "",
    reason: str = "",
    **counts,
) -> None:
    """Пишет только идентификаторы, фиксированные коды решений и числовые счётчики."""
    execution = current_vision_execution.get()
    payload = {
        "job": execution.job_key if execution is not None else "unscoped",
        "stage": stage,
        "candidate_id": candidate_id,
        "page": page,
        "decision": decision,
        "reason": reason,
        **counts,
    }
    logger.info(
        "engineering_candidate %s",
        json.dumps(payload, sort_keys=True, ensure_ascii=False),
    )
