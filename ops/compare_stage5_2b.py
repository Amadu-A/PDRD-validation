# ops/compare_stage5_2b.py
"""Сравнивает короткие прогоны PDF по фактам, не по перефразировкам.

Получает идентификаторы документов после UI-прогонов. Скрипт выполняется
внутри api-gateway через stdin для доступа к существующему /data/analyses.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

_DUPLICATE = re.compile(r"p\d+-dpos-(\d+(?:-\d+){2,4})$")


def main() -> int:
    """Сверяет сохранённые результаты и возвращает статус совпадения."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/data/analyses"))
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--pages", nargs="+", type=int, default=[22, 23])
    parser.add_argument(
        "--require-tag", action="append", default=[], metavar="PAGE:TAG"
    )
    args = parser.parse_args()
    if len(set(args.ids)) != len(args.ids):
        parser.error("Each run must have a different document ID")
    required_tags: set[tuple[int, str]] = set()
    for value in args.require_tag:
        try:
            page_text, tag = value.split(":", 1)
            page = int(page_text)
        except ValueError:
            parser.error(f"Invalid required tag: {value}")
        if (
            page not in args.pages
            or _DUPLICATE.fullmatch(f"p{page}-dpos-{tag.replace('.', '-')}") is None
        ):
            parser.error(f"Invalid required tag: {value}")
        required_tags.add((page, tag))

    run_tags: list[set[tuple[int, str]]] = []
    missing_required_by_run: list[set[tuple[int, str]]] = []
    duplicate_ids_by_run: list[list[tuple[int, str]]] = []
    for number, document_id in enumerate(args.ids, start=1):
        result_path = args.root / document_id / "result.json"
        if not result_path.is_file():
            parser.error(f"Result not found: {result_path}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        findings = result.get("findings") or []
        if not isinstance(findings, list):
            parser.error(f"Malformed findings: {result_path}")
        selected = [
            item
            for item in findings
            if isinstance(item, dict) and int(item.get("page", 0) or 0) in args.pages
        ]
        tags: set[tuple[int, str]] = set()
        dup_counts: Counter[tuple[int, str]] = Counter()
        for item in selected:
            match = _DUPLICATE.fullmatch(str(item.get("finding_id", "")))
            if match is None:
                continue
            key = (int(item["page"]), match.group(1).replace("-", "."))
            tags.add(key)
            dup_counts[key] += 1
        run_tags.append(tags)
        missing_required = required_tags - tags
        missing_required_by_run.append(missing_required)
        print(f"RUN {number}: {document_id} selected_findings={len(selected)}")
        for page in args.pages:
            page_items = [item for item in selected if int(item["page"]) == page]
            print(f"  page={page} findings={len(page_items)}")
            for item in page_items:
                print(f"    {item.get('finding_id')}: {item.get('comment')}")
        print("  DETERMINISTIC_TAGS:", sorted(tags))
        print("  MISSING_REQUIRED_TAGS:", sorted(missing_required))
        duplicates = [key for key, count in dup_counts.items() if count != 1]
        duplicate_ids_by_run.append(duplicates)
        print("  DUPLICATE_CANONICAL_IDS:", duplicates)
        # SHA отражает точное содержимое, а не семантическое совпадение.
        canonical = json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()
        print("  SELECTED_RESULT_SHA256:", hashlib.sha256(canonical).hexdigest())
    baseline = run_tags[0]
    identical = all(tags == baseline for tags in run_tags[1:])
    required_present = not any(missing_required_by_run)
    canonical_ids_unique = not any(duplicate_ids_by_run)
    print("DETERMINISTIC_TAG_SET_IDENTICAL:", identical)
    print("REQUIRED_TAGS_PRESENT_IN_EVERY_RUN:", required_present)
    print("CANONICAL_IDS_UNIQUE_IN_EVERY_RUN:", canonical_ids_unique)
    for number, tags in enumerate(run_tags[1:], start=2):
        print(
            f"RUN1 vs RUN{number}: "
            f"missing={sorted(baseline - tags)} extra={sorted(tags - baseline)}"
        )
    return 0 if identical and required_present and canonical_ids_unique else 1


if __name__ == "__main__":
    raise SystemExit(main())
