# tests/architecture/test_frontend_review.py

"""Границы Human Review и исполняемые JS-тесты, включая ручную геометрию."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_review_has_independent_state_view_and_controller() -> None:
    """Закрепляет независимость модели, геометрии, UI и CSS-блоков."""
    feature = FRONTEND / "src/js/features/review"
    state = (feature / "state.js").read_text(encoding="utf-8")
    controls = (feature / "controls.js").read_text(encoding="utf-8")
    controller = (feature / "controller.js").read_text(encoding="utf-8")
    geometry = (feature / "geometry.js").read_text(encoding="utf-8")
    manual = (feature / "manual.js").read_text(encoding="utf-8")
    app = (FRONTEND / "src/js/app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "src/css/style.css").read_text(encoding="utf-8")

    assert "document." not in state
    assert "document." not in geometry
    assert "fetch(" not in state
    assert "fetch(" not in controls
    assert "fetch(" not in manual
    assert "createReviewController" in app
    assert "createReviewState" in controller
    assert "createManualAnnotationController" in controller
    assert "normalizedPoint" in manual
    assert '"./blocks/review.css"' in css
    assert '"./blocks/manual-annotation.css"' in css
    assert "innerHTML" not in controller
    assert "innerHTML" not in controls
    assert "innerHTML" not in manual


def test_review_javascript_behaviour() -> None:
    """Запускает все JS-тесты при общем Python quality gate."""
    node = shutil.which("node")
    assert node is not None, "Для frontend review tests необходим Node.js 18+."

    suites = sorted((FRONTEND / "tests").glob("*.test.js"))
    assert suites, "Отсутствуют исполняемые frontend tests."

    result = subprocess.run(
        [node, "--test", *(str(suite) for suite in suites)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
