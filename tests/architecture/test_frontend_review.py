# tests/architecture/test_frontend_review.py

"""Frontend review boundaries и обязательные исполняемые JS-тесты."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_review_has_independent_state_view_and_controller() -> None:
    """Закрепляет раздельные обязанности нового frontend feature."""
    feature = FRONTEND / "src/js/features/review"
    state = (feature / "state.js").read_text(encoding="utf-8")
    controls = (feature / "controls.js").read_text(encoding="utf-8")
    controller = (feature / "controller.js").read_text(encoding="utf-8")
    app = (FRONTEND / "src/js/app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "src/css/style.css").read_text(encoding="utf-8")

    assert "document." not in state
    assert "fetch(" not in state
    assert "fetch(" not in controls
    assert "createReviewController" in app
    assert "createReviewState" in controller
    assert '"./blocks/review.css"' in css
    assert "innerHTML" not in controller
    assert "innerHTML" not in controls


def test_review_javascript_behaviour() -> None:
    """Запускает unit и DOM-integration tests также из общего pytest gate."""
    node = shutil.which("node")
    assert node is not None, "Для frontend review tests необходим Node.js 18+."
    result = subprocess.run(
        [
            node,
            "--test",
            str(FRONTEND / "tests/review-state.test.js"),
            str(FRONTEND / "tests/review-controller.test.js"),
            str(FRONTEND / "tests/review-result.test.js"),
        ],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
