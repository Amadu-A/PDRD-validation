# tests/architecture/test_stage5_compare.py

"""Контрольный прогон не должен проходить при одинаково пустых повторах."""

import json
import subprocess
import sys
from pathlib import Path


def test_required_duplicate_must_exist_in_every_run(tmp_path: Path) -> None:
    """Совпадение пустых наборов не доказывает восстановление 8.9.3."""
    script = Path(__file__).resolve().parents[2] / "ops" / "compare_stage5_2b.py"
    for document_id in ("first", "second"):
        directory = tmp_path / document_id
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps({"findings": []}), encoding="utf-8"
        )

    command = [
        sys.executable,
        str(script),
        "--root",
        str(tmp_path),
        "--ids",
        "first",
        "second",
        "--pages",
        "22",
        "--require-tag",
        "22:8.9.3",
    ]
    empty = subprocess.run(command, capture_output=True, text=True, check=False)
    assert empty.returncode == 1
    assert "REQUIRED_TAGS_PRESENT_IN_EVERY_RUN: False" in empty.stdout

    for document_id in ("first", "second"):
        (tmp_path / document_id / "result.json").write_text(
            json.dumps(
                {
                    "findings": [
                        {"finding_id": "p22-dpos-8-9-3", "page": 22, "comment": ""}
                    ]
                }
            ),
            encoding="utf-8",
        )
    complete = subprocess.run(command, capture_output=True, text=True, check=False)
    assert complete.returncode == 0
    assert "REQUIRED_TAGS_PRESENT_IN_EVERY_RUN: True" in complete.stdout

    (tmp_path / "second" / "result.json").write_text(
        json.dumps(
            {
                "findings": [
                    {"finding_id": "p22-dpos-8-9-3", "page": 22, "comment": ""},
                    {"finding_id": "p22-dpos-8-9-3", "page": 22, "comment": ""},
                ]
            }
        ),
        encoding="utf-8",
    )
    duplicated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert duplicated.returncode == 1
    assert "CANONICAL_IDS_UNIQUE_IN_EVERY_RUN: False" in duplicated.stdout
