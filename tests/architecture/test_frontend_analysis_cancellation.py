# tests/architecture/test_frontend_analysis_cancellation.py

"""Architecture guards frontend cancellation UX Stage 8.3."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND_SOURCE = ROOT / "frontend" / "src"

API_PATH = FRONTEND_SOURCE / "js" / "features" / "analysis" / "api.js"

POLLING_PATH = FRONTEND_SOURCE / "js" / "features" / "analysis" / "polling.js"

CONTROLLER_PATH = FRONTEND_SOURCE / "js" / "features" / "analysis" / "controller.js"

MODAL_PATH = FRONTEND_SOURCE / "js" / "components" / "modal.js"

MODAL_CSS_PATH = FRONTEND_SOURCE / "css" / "blocks" / "analysis-modal.css"


def _read(
    path: Path,
) -> str:
    """Читает frontend source."""
    return path.read_text(
        encoding="utf-8",
    )


def test_frontend_exposes_analysis_cancel_api() -> None:
    """Browser отменяет analysis только через API Gateway."""
    source = _read(
        API_PATH,
    )

    assert "export async function cancelAnalysis(" in source

    assert "${ANALYSES_ENDPOINT}/${jobId}/cancel" in source

    assert 'method: "POST"' in source


def test_cancelled_polling_is_terminal_but_not_error() -> None:
    """Штатная отмена возвращается controller как terminal state."""
    source = _read(
        POLLING_PATH,
    )

    assert 'payload.status === "cancelled"' in source

    assert 'payload.status === "completed"' in source

    assert '|| payload.status === "cancelled"' in source

    assert '"Анализ был отменён."' not in source


def test_controller_does_not_request_result_for_cancelled_job() -> None:
    """Cancelled analysis заканчивается neutral UI без GET result."""
    source = _read(
        CONTROLLER_PATH,
    )

    assert "cancelAnalysis," in source

    assert "modal.setCancelHandler(" in source

    assert 'finalStatus.status === "cancelled"' in source

    cancelled_position = source.index(
        'finalStatus.status === "cancelled"',
    )

    result_position = source.index(
        "const payload = await getAnalysisResult(",
    )

    assert cancelled_position < result_position

    assert "Анализ остановлен по запросу пользователя." in source


def test_modal_owns_visible_cancel_control() -> None:
    """Cancel button появляется только после получения job_id."""
    source = _read(
        MODAL_PATH,
    )

    assert 'cancelButton.textContent = (\n    "Отменить анализ"\n  );' in source

    assert (
        "cancelButton.className = (\n"
        '    "analysis-modal__cancel-button"\n'
        "  );" in source
    )

    assert "cancelButton.hidden = false;" in source

    assert "function setCancelHandler(" in source

    assert "function setCancelling(" in source


def test_cancel_button_uses_existing_danger_tokens() -> None:
    """Cancellation control визуально отделён от ошибок результата."""
    source = _read(
        MODAL_CSS_PATH,
    )

    assert ".analysis-modal__cancel-button" in source

    assert "var(--color-danger)" in source

    assert "var(--color-danger-soft)" in source
