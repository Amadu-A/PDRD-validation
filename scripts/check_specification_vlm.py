# scripts/check_specification_vlm.py

"""Проверяет PDF-спецификацию и весь understanding stage на настоящем shared-vlm."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
for service in ("analysis-service", "document-service"):
    sys.path.insert(0, str(ROOT / "services" / service / "src"))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from pdrd_analysis_service.application.limited_vision import (  # noqa: E402
    LimitedVisionModel,
)
from pdrd_analysis_service.application.use_cases.understanding import (  # noqa: E402
    UnderstandPage,
)
from pdrd_analysis_service.core.container import build_container  # noqa: E402
from pdrd_analysis_service.domain.equipment_specification import (  # noqa: E402
    specification_report,
)
from pdrd_analysis_service.infrastructure.vllm import (  # noqa: E402
    VllmStructuredVisionModel,
)
from pdrd_analysis_service.transport.http.dependencies import (  # noqa: E402
    get_container,
)
from pdrd_analysis_service.transport.http.stage_batch_routes import router  # noqa: E402
from pdrd_document_service.core.settings import PdfSettings  # noqa: E402
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader  # noqa: E402


def write_json(path: Path, value: object) -> None:
    """Сохраняет полный результат без вывода исходного документа в консоль."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class RecordedVisionModel:
    """Записывает метрики реального адаптера, не заменяя ответы модели."""

    def __init__(self, delegate, directory: Path) -> None:
        """Подготавливает отдельный каталог конкретного прогона."""
        self.delegate = delegate
        self.directory = directory
        self.calls = []
        self.completed_pages = []

    async def generate_json(self, **request):
        """Публикует краткий прогресс и сохраняет полную метрику вызова."""
        entry = {
            "stage": request["stage"],
            "image": request.get("image_bytes") is not None,
            "prompt_characters": len(request["prompt"]),
            "equipment_requested": "equipment_identities"
            in request["schema"]["properties"],
        }
        self.calls.append(entry)
        self._progress()
        try:
            result = await self.delegate.generate_json(**request)
        except Exception as error:
            entry.update(error_type=type(error).__name__)
            if getattr(error, "metrics", None) is not None:
                entry["metrics"] = error.metrics.as_dict()
            self._progress()
            raise
        entry["metrics"] = result.metrics.as_dict()
        if request["stage"].startswith("page_understanding:"):
            self.completed_pages.append(int(request["stage"].split(":")[1]))
        self._progress()
        return result

    async def is_ready(self):
        """Проверяет тот же resident runtime."""
        return await self.delegate.is_ready()

    def _progress(self):
        """Сохраняет прогресс в файл для короткой диагностики долгого GPU-прогона."""
        write_json(
            self.directory / "progress.json",
            {
                "completed_pages": self.completed_pages,
                "calls": self.calls,
            },
        )


async def run(arguments) -> int:
    """Выполняет production PDF reader, HTTP stage и независимую проверку полноты."""
    directory = arguments.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=directory / "vlm.log", level=logging.INFO, encoding="utf-8"
    )
    print(f"Результаты: {directory}", flush=True)
    content = arguments.pdf.read_bytes()
    settings = PdfSettings()
    reader = PyMuPdfReader(
        render_max_side=settings.render_max_side, text_limit=settings.text_limit
    )
    count = reader.get_page_count(content)
    selected = (
        tuple(range(1, count + 1)) if not arguments.pages else tuple(arguments.pages)
    )
    document = reader.extract(
        content, selected_pages=selected, include_equipment_specification=True
    )
    container = build_container()
    vlm_settings = container.settings.vlm
    adapter = VllmStructuredVisionModel(
        base_url=arguments.base_url,
        model=vlm_settings.model,
        request_timeout_seconds=vlm_settings.request_timeout_seconds,
        connect_timeout_seconds=vlm_settings.connect_timeout_seconds,
        health_timeout_seconds=vlm_settings.health_timeout_seconds,
        max_attempts=arguments.max_attempts or vlm_settings.max_attempts,
        retry_backoff_seconds=vlm_settings.retry_backoff_seconds,
        max_retry_num_predict=arguments.max_retry_num_predict
        or vlm_settings.max_retry_num_predict,
    )
    recorder = RecordedVisionModel(
        LimitedVisionModel(adapter, max_concurrent=1), directory
    )
    if not await recorder.is_ready():
        raise RuntimeError("Настоящий shared-vlm не прошёл readiness.")
    pipeline = container.settings.pipeline.model_copy(
        update={
            "vlm_stage_concurrency": 1,
            "page_facts_num_predict": arguments.page_num_predict
            or container.settings.pipeline.page_facts_num_predict,
        }
    )
    container = replace(
        container,
        settings=container.settings.model_copy(update={"pipeline": pipeline}),
        understand_page=UnderstandPage(
            recorder, pipeline.page_facts_num_predict, pipeline.max_facts_per_page
        ),
        analysis_progress_probe=None,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_container] = lambda: container
    payload = {
        "document_id": str(uuid4()),
        "items": [
            {
                "page_number": page.number,
                "heuristic_page_type": page.page_type,
                "extracted_text": page.text,
                "image_base64": base64.b64encode(page.rendered_png).decode("ascii"),
                "equipment_specification": page.equipment_specification,
                "use_equipment_web_search": True,
                "use_document_context": arguments.document_context,
            }
            for page in document.pages
        ],
    }
    started = time.monotonic()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages", json=payload
        )
    write_json(directory / "stage-response.json", response.json())
    response.raise_for_status()
    outputs = response.json()["items"]
    assert [item["page_number"] for item in outputs] == list(selected), (
        "Потеря/перестановка страниц"
    )
    report = specification_report(
        [
            {"page_number": item["page_number"], "facts": item["result"]["facts"]}
            for item in outputs
        ]
    )
    sources = [
        page.equipment_specification
        for page in document.pages
        if page.equipment_specification
    ]
    expected = [item["item_id"] for spec in sources for item in spec["items"]]
    assert [item["item_id"] for item in report["structured_items"]] == expected, (
        "Потеря/перестановка позиций"
    )
    assert report["coverage_complete"], "Контроль полноты исходных строк не пройден"
    table_pages = {
        spec["page"]
        for spec in sources
        if spec["status"] == "reliable" and spec["coverage_complete"]
    }
    assert all(
        not call["equipment_requested"]
        for call in recorder.calls
        if call["stage"].startswith("page_understanding:")
        and int(call["stage"].split(":")[1]) in table_pages
    )
    if arguments.require_programmatic_tables:
        assert all(
            not call["stage"].startswith("page_understanding:")
            or int(call["stage"].split(":")[1]) not in table_pages
            for call in recorder.calls
        ), "Таблица повторно передана в основной Understanding"
        assert all(
            item["result"]["metrics"]["understanding"]["route"] == "pdf_table"
            for item in outputs
            if item["page_number"] in table_pages
        )
    diagnostics = [
        item["result"]["metrics"].get("understanding", {}) for item in outputs
    ]

    def tokens(field):
        """Суммирует известные usage всех попыток; неизвестные значения не выдаёт за ноль."""
        values = [call.get("metrics", {}).get(field) for call in recorder.calls]
        return None if any(value is None for value in values) else sum(values)

    write_json(directory / "specification.json", report)
    summary = {
        "pdf_sha256": hashlib.sha256(content).hexdigest(),
        "http_status": response.status_code,
        "pages": len(outputs),
        "table_pages": sorted(table_pages),
        "raw_rows": report["raw_row_count"],
        "items": report["item_count"],
        "coverage_complete": report["coverage_complete"],
        "real_vlm_calls": len(recorder.calls),
        "failed_calls": sum("error_type" in call for call in recorder.calls),
        "provider_attempts": sum(
            call.get("metrics", {}).get("attempt", 0) for call in recorder.calls
        ),
        "cache_enabled": False,
        "document_context": arguments.document_context,
        "page_num_predict": pipeline.page_facts_num_predict,
        "max_attempts": arguments.max_attempts or vlm_settings.max_attempts,
        "max_retry_num_predict": arguments.max_retry_num_predict
        or vlm_settings.max_retry_num_predict,
        "input_tokens": tokens("prompt_eval_count"),
        "output_tokens": tokens("eval_count"),
        "programmatic_pages": [
            item["page_number"]
            for item in diagnostics
            if item.get("route") == "pdf_table"
        ],
        "programmatic_facts": sum(
            item.get("programmatic_facts", 0) for item in diagnostics
        ),
        "seconds": round(time.monotonic() - started, 2),
    }
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    """Принимает адрес runtime и PDF; не меняет .env, Git или настройки сервера."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf", type=Path, default=ROOT / "tests/fixtures/equipment/ios-4-1-7.pdf"
    )
    parser.add_argument("--base-url", default="http://shared-vlm:8000/v1")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.mkdtemp(prefix="pdrd-spec-vlm-")),
    )
    parser.add_argument("--pages", type=int, nargs="+")
    parser.add_argument("--document-context", action="store_true")
    parser.add_argument(
        "--page-num-predict", type=int, choices=range(1, 16001), metavar="1..16000"
    )
    parser.add_argument("--max-attempts", type=int, choices=range(1, 4))
    parser.add_argument(
        "--max-retry-num-predict", type=int, choices=range(1, 16001), metavar="1..16000"
    )
    parser.add_argument("--require-programmatic-tables", action="store_true")
    arguments = parser.parse_args()
    try:
        return asyncio.run(run(arguments))
    except Exception as error:
        write_json(
            arguments.output_dir / "failure.json",
            {"type": type(error).__name__, "message": str(error)},
        )
        print(f"Ошибка: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
