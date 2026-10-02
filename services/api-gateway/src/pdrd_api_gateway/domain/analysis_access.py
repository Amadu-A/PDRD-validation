# services/api-gateway/src/pdrd_api_gateway/domain/analysis_access.py

"""Проверка доступа к результату анализа по владельцу или гостевой ссылке.

Разрешение Review проверяется отдельно от операционного permission. Передать
`scoped_authorized=True` может только серверная проверка области в user-service.
"""

import secrets
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from pdrd_api_gateway.domain.analysis_job import AnalysisJob


def can_access_analysis_job(
    job: AnalysisJob,
    *,
    actor_user_id: UUID | None,
    guest_access_token: str | None,
    now: datetime,
    review: bool = False,
    scoped_authorized: bool = False,
) -> bool:
    """Отклоняет чужие, просроченные и старые job без записи о владельце."""
    if job.owner_user_id is not None:
        return actor_user_id == job.owner_user_id or (
            actor_user_id is not None and scoped_authorized
        )

    if review or not guest_access_token or len(guest_access_token) > 256:
        return False

    token_hash = job.guest_access_token_hash
    expires_at = job.guest_access_expires_at
    if token_hash is None or expires_at is None or expires_at.tzinfo is None:
        return False
    if now.tzinfo is None or now >= expires_at:
        return False

    supplied_hash = sha256(guest_access_token.encode("utf-8")).hexdigest()
    return secrets.compare_digest(supplied_hash, token_hash)
