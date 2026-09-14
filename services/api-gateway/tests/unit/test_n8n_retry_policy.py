# services/api-gateway/tests/unit/test_n8n_retry_policy.py

"""Unit tests whole-workflow retry policy API Gateway."""

import pytest
from pdrd_api_gateway.infrastructure.orchestration.n8n import (
    _is_retryable_http_status,
)


@pytest.mark.parametrize(
    "status_code",
    [
        408,
        425,
        429,
        502,
        503,
        504,
    ],
)
def test_ingress_transient_statuses_remain_retryable(
    status_code: int,
) -> None:
    """Недоступность n8n ingress сохраняет controlled whole-job retry."""
    assert _is_retryable_http_status(
        status_code,
    )


@pytest.mark.parametrize(
    "status_code",
    [
        400,
        401,
        404,
        409,
        422,
        500,
    ],
)
def test_workflow_execution_errors_are_terminal(
    status_code: int,
) -> None:
    """Ошибка уже запущенного workflow не должна повторять весь анализ."""
    assert not _is_retryable_http_status(
        status_code,
    )


def test_thousand_synthetic_workflow_500_errors_do_not_requeue() -> None:
    """Synthetic load guard запрещает retry storm для n8n workflow HTTP 500."""
    statuses = [
        500
        for _ in range(
            1000,
        )
    ]

    retryable = [
        status_code
        for status_code in statuses
        if _is_retryable_http_status(
            status_code,
        )
    ]

    assert retryable == []
