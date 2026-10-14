# scripts/check_normative_vlm.py

"""Проверяет короткие нормативные ответы на настоящем GPU, включая экспорт Input n8n."""

import argparse
import asyncio
import base64
import json
import logging
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from check_specification_vlm import ROOT, RecordedVisionModel, write_json

import httpx
from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
from pdrd_analysis_service.application.ports.vision_model import VisionModelOutputBudgetError
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.core.container import build_container
from pdrd_analysis_service.infrastructure.vllm import VllmStructuredVisionModel
from pdrd_analysis_service.main import create_app
from pdrd_analysis_service.transport.http.schemas import CheckNormsRequest
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader


class RecordedNormativeModel(RecordedVisionModel):
    """Дополняет общий журнал размером каждой порции и метриками исчерпанного ответа."""

    async def generate_json(self, **request):
        """Проверяет реальный adapter и оставляет полные сведения о восстановлении."""
        index = len(self.calls)
        try:
            return await super().generate_json(**request)
        except VisionModelOutputBudgetError as error:
            if error.metrics:
                self.calls[index]["metrics"] = error.metrics.as_dict()
            raise
        finally:
            self.calls[index]["candidate_capacity"] = (
                request["schema"]["properties"].get("violations", {}).get("maxItems")
            )
            self._progress()


async def run(arguments):
    """Исполняет настоящий HTTP Norms stage; основной результат и метрики сохраняет в /tmp."""
    directory = arguments.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / "vlm.log", level=logging.INFO, encoding="utf-8")
    print(f"Результаты: {directory}", flush=True)
    container = build_container()
    settings = container.settings.vlm
    adapter = VllmStructuredVisionModel(
        base_url=arguments.base_url,
        model=settings.model,
        request_timeout_seconds=settings.request_timeout_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        health_timeout_seconds=settings.health_timeout_seconds,
        max_attempts=settings.max_attempts,
        retry_backoff_seconds=settings.retry_backoff_seconds,
        max_retry_num_predict=settings.max_retry_num_predict,
    )
    model = RecordedNormativeModel(LimitedVisionModel(adapter, max_concurrent=1), directory)
    if not await model.is_ready():
        raise RuntimeError("shared-vlm не прошёл readiness.")
    page = (
        PyMuPdfReader(render_max_side=2400, text_limit=12000)
        .extract(arguments.pdf.read_bytes(), selected_pages=(arguments.page,))
        .pages[0]
    )
    if arguments.input:
        payload = json.loads(arguments.input.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            payload = payload[0]
        payload = payload.get("json", payload)
        items = payload.get("items", [payload])
        selected = next(item for item in items if int(item["page_number"]) == arguments.page)
        request = {key: value for key, value in selected.items() if key in CheckNormsRequest.model_fields}
        mode = "n8n_input"
    else:
        if arguments.understanding:
            understanding = json.loads(arguments.understanding.read_text(encoding="utf-8-sig"))
            facts = next(
                item["result"]["facts"] for item in understanding["items"] if item["page_number"] == arguments.page
            )
        else:
            facts, _ = await UnderstandPage(model, 3200).execute(
                page_number=page.number,
                heuristic_page_type=page.page_type,
                extracted_text=page.text,
                image_bytes=page.rendered_png,
                use_document_context=False,
                use_equipment_web_search=True,
            )
            from dataclasses import asdict

            facts = asdict(facts)
        request = {
            "page_number": page.number,
            "page_facts": facts,
            "extracted_text": page.text,
            "normative_sources": [],
            "technical_assignment_sources": [],
            "user_package_sources": [],
        }
        mode = "pdf_without_external_sources"
    request.setdefault("image_base64", base64.b64encode(page.rendered_png).decode("ascii"))
    CheckNormsRequest.model_validate(request)
    write_json(directory / "input.json", request)
    pipeline = container.settings.pipeline.model_copy(update={"vlm_stage_concurrency": 1})
    use_case = replace(container.check_page_against_norms, vision_model=model)
    container = replace(
        container,
        check_page_against_norms=use_case,
        analysis_progress_probe=None,
        settings=container.settings.model_copy(update={"pipeline": pipeline}),
    )
    app = create_app(container)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance") as client:
        response = await client.post(
            "/internal/v1/stages/check-norms", json={"document_id": str(uuid4()), "items": [request]}
        )
    write_json(directory / "stage-response.json", response.json())
    response.raise_for_status()
    normative_calls = [call for call in model.calls if call["stage"].startswith("normative_check:")]
    assert normative_calls and all(call["candidate_capacity"] <= 10 for call in normative_calls)
    findings = response.json()["items"][0]["result"]["findings"]
    assert len({item["finding_id"] for item in findings}) == len(findings)
    summary = {
        "input_mode": mode,
        "page": arguments.page,
        "http_status": response.status_code,
        "findings": len(findings),
        "normative_calls": len(normative_calls),
        "cache_enabled": False,
        "source_counts": {
            key: len(request.get(key, []))
            for key in (
                "normative_sources",
                "technical_assignment_sources",
                "user_package_sources",
                "document_context_sources",
            )
        },
        "capacities": [call["candidate_capacity"] for call in normative_calls],
        "recovered_budget_errors": sum(
            call.get("error_type") == "VisionModelOutputBudgetError" for call in normative_calls
        ),
        "output_tokens": sum(call.get("metrics", {}).get("eval_count", 0) or 0 for call in normative_calls),
    }
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    """Не меняет .env или рабочие задания; без --input явно отмечает отсутствие N/T/U."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--understanding", type=Path)
    parser.add_argument("--pdf", type=Path, default=ROOT / "tests/fixtures/equipment/ios-4-1-7.pdf")
    parser.add_argument("--page", type=int, default=22)
    parser.add_argument("--base-url", default="http://shared-vlm:8000/v1")
    parser.add_argument("--output-dir", type=Path, default=Path(tempfile.mkdtemp(prefix="pdrd-norm-vlm-")))
    arguments = parser.parse_args()
    try:
        asyncio.run(run(arguments))
    except Exception as error:
        write_json(arguments.output_dir / "failure.json", {"type": type(error).__name__, "message": str(error)})
        print(f"Ошибка: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
