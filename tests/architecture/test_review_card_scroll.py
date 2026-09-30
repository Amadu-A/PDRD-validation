# tests/architecture/test_review_card_scroll.py

"""Закрепляет отсутствие прокрутки Gold-карточки после восстановления с сервера."""

import re
from pathlib import Path


def test_gold_container_does_not_override_non_scrolling_review_content():
    """Рамка остаётся растягиваемой, переполнение текста скрывается внутренним блоком."""
    css = Path(__file__).resolve().parents[2] / "frontend/src/css/blocks"
    manual = (css / "manual-annotation.css").read_text(encoding="utf-8")
    content = (css / "review-resize.css").read_text(encoding="utf-8")
    card = re.search(r"\.manual-annotation__card\s*\{([^}]+)\}", manual).group(1)
    inner = re.search(r"\.review-resize__content\s*\{([^}]+)\}", content).group(1)
    assert re.search(r"overflow:\s*visible\s*;", card)
    assert re.search(r"overflow:\s*hidden\s*;", inner)
