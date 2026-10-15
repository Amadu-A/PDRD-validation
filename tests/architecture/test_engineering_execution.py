# tests/architecture/test_engineering_execution.py

"""Границы benchmark, координат, отмены и общего распределения VLM между заданиями."""

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_assertion_domain_has_no_runtime_or_transport_dependencies():
    """Общий предмет проверки остаётся в domain и не зависит от HTTP, БД или модели."""
    path = (
        ROOT
        / "services/analysis-service/src/pdrd_analysis_service/domain/engineering_assertion.py"
    )
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "pdrd_analysis_service"
        ):
            assert node.module.startswith("pdrd_analysis_service.domain.")
        if isinstance(node, ast.Import):
            assert not {alias.name.split(".")[0] for alias in node.names} & {
                "httpx",
                "fastapi",
                "sqlalchemy",
            }


def test_expert_matrix_cannot_enter_runtime_prompts() -> None:
    """Эталонные замечания служат оценке; production packages не загружают fixture."""
    forbidden = (
        "review-cases.json",
        "expert_issue",
        "human_annotations",
        "H01",
        "ПГ1–ПГ5",
    )
    for path in (ROOT / "services/analysis-service/src").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert not any(item in content for item in forbidden), path
    matrix = json.loads(
        (ROOT / "tests/fixtures/engineering/review-cases.json").read_text(
            encoding="utf-8-sig"
        )
    )
    assert len(matrix["cases"]) == 35
    assert len({case["case_id"] for case in matrix["cases"]}) == 35
    assert all(
        case["review_status"] == "needs_engineer_adjudication"
        for case in matrix["cases"]
    )


def test_all_gpu_responses_stop_cancellation_before_result_building() -> None:
    """Каждый штатный отменённый HTTP-ответ выходит в общий webhook отмены."""
    for path in (ROOT / "n8n/workflows").glob("analysis-v2*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        nodes = {node["name"]: node for node in workflow["nodes"]}
        connections = workflow["connections"]
        for name, node in nodes.items():
            if node["type"] != "n8n-nodes-base.httpRequest":
                continue
            url = node["parameters"].get("url", "")
            gpu = any(
                part in url
                for part in (
                    "/stages/",
                    "/pages/understand",
                    "/pages/check-",
                    "/findings/finalize",
                    "/document-context/check-consistency",
                )
            )
            if not gpu:
                continue
            guard = f"Continue {name} Response"
            assert connections[name]["main"] == [
                [{"node": guard, "type": "main", "index": 0}]
            ]
            assert nodes[guard]["type"] == "n8n-nodes-base.if"
            condition = nodes[guard]["parameters"]["conditions"]["conditions"][0]
            assert (
                "cancelled" in condition["leftValue"]
                and condition["operator"]["operation"] == "notEquals"
            )
            assert connections[guard]["main"][1] == [
                {"node": "Build Cancelled Result", "type": "main", "index": 0}
            ]
            assert node.get("onError") is None
            assert "neverError" not in json.dumps(node["parameters"])
        stage = nodes["Progress Technical Assignment"]["parameters"]["body"]
        assert (
            "checking_technical_assignment" in stage
            and "understanding_sheet" not in stage
        )
        assert (
            connections["Has T First Pass"]["main"][0][0]["node"]
            == "Progress Technical Assignment"
        )


def test_crops_depend_on_port_and_domain_has_no_image_driver() -> None:
    """Выбор фрагмента — application, растровая обрезка — infrastructure, матрица — domain."""
    application = (
        ROOT
        / "services/analysis-service/src/pdrd_analysis_service/application/graphical_refinement.py"
    ).read_text("utf-8")
    domain = (
        ROOT
        / "services/analysis-service/src/pdrd_analysis_service/domain/image_crop.py"
    ).read_text("utf-8")
    assert (
        "application.ports.image_cropper" in application
        and "from PIL" not in application
    )
    assert "PIL" not in domain and "httpx" not in domain
    package = tomllib.loads(
        (ROOT / "services/analysis-service/pyproject.toml").read_text("utf-8")
    )
    assert "Pillow==11.3.0" in package["project"]["dependencies"]
