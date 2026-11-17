# tests/functional/test_review_browser_geometry.py

"""Геометрия в отдельном headless Edge и настоящем HTTP Review, без рабочего Chrome.

Проверка использует изолированные хранилища функционального стенда; она не является
приёмкой промышленной БД или текущей пользовательской сессии. Включение: PDRD_BROWSER_E2E=1.
"""

import asyncio
import base64
import functools
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
import httpx
import pytest

from tests.functional.test_review_api import CARD, UI_KEY
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow

pytestmark = pytest.mark.skipif(
    os.environ.get("PDRD_BROWSER_E2E") != "1" or sys.platform != "win32",
    reason="Браузерная проверка запускается явно в изолированном Edge на Windows.",
)
ROOT = Path(__file__).parents[2]


@pytest.fixture
def review_browser_server(pdf_flow):
    """Публикует штатный frontend и изолированные ASGI Gateway/Review/Document через localhost."""
    f = pdf_flow
    payload = asyncio.run(f.artifacts.load_result(document_id=f.jobs.job.document_id))
    payload.update(
        status="completed",
        source_mode="pdf_only",
        summary="Изолированная проверка Review",
    )
    visualization = asyncio.run(
        f.gateway.get_review_source.visualizations.execute(job_id=f.job_id)
    )
    with fitz.open(stream=f.original) as document:
        visualization["pages"][0]["image_base64"] = base64.b64encode(
            document[0].get_pixmap().tobytes("png")
        ).decode()

    async def seed():
        """Создаёт Gold вдали от автоматической рамки, чтобы не смешивать совпавшие hit targets."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(f.network.apps["gateway"]),
            base_url="http://fixture",
            headers={"X-PDRD-Review-Key": UI_KEY},
        ) as client:
            response = await client.post(f.endpoint + "/open")
            assert response.status_code == 200
            response = await client.post(
                f.endpoint + "/commands",
                json={
                    "action": "add",
                    "finding_id": "manual:00000000-0000-4000-8000-000000000001",
                    "expected_revision": response.json()["revision"],
                    "page_number": 1,
                    "text": "Ручное замечание Gold",
                    "normative_basis": "СП 1",
                    "issue_box": {
                        "x_min": 700,
                        "y_min": 50,
                        "x_max": 800,
                        "y_max": 140,
                    },
                    "callout_box": CARD,
                },
            )
            assert response.status_code == 200, response.text

    asyncio.run(seed())
    html = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Изолированный Review</title>
<link rel="stylesheet" href="/css/style.css"><main id="root"></main><script type="module">
import {renderAnalysisReport} from '/js/features/analysis/report.js';
import {createReviewPersistence} from '/js/features/review/persistence.js';
const data=await (await fetch('/fixture-data')).json();const root=document.querySelector('#root');
root.append(renderAnalysisReport(data.payload,{jobId:data.job,visualization:data.visualization}));
createReviewPersistence().mount(root,{jobId:data.job});
</script></html>"""

    class Handler(SimpleHTTPRequestHandler):
        """Направляет только тестовые /api в уже изолированный HTTP-стенд."""

        def log_message(self, *args):
            """Не засоряет успешный тест журналом загрузки статических файлов."""

        def do_GET(self):
            """Отдаёт страницу, снимок результатов или штатный GET API."""
            if self.path.split("?")[0] == "/fixture":
                self.send_bytes(
                    200, html.encode(), {"Content-Type": "text/html; charset=utf-8"}
                )
            elif self.path == "/fixture-data":
                self.send_bytes(
                    200,
                    json.dumps(
                        {
                            "job": str(f.job_id),
                            "payload": payload,
                            "visualization": visualization,
                        }
                    ).encode(),
                    {"Content-Type": "application/json"},
                )
            elif self.path.startswith("/api/"):
                self.proxy("GET")
            else:
                super().do_GET()

        def do_POST(self):
            """Передаёт пользовательскую CAS-команду в штатный обработчик."""
            self.proxy("POST")

        def send_bytes(self, status, content, headers):
            """Возвращает только необходимые безопасные заголовки тестового ответа."""
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() in {
                    "content-type",
                    "content-disposition",
                    "cache-control",
                }:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def proxy(self, method):
            """Использует выделенный контекст тестового пользователя, не промышленную авторизацию."""
            body = (
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if method == "POST"
                else None
            )

            async def request():
                """Выполняет существующий REST-контракт с проверкой Origin и серверного ключа стенда."""
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(f.network.apps["gateway"]),
                    base_url=base,
                ) as client:
                    return await client.request(
                        method,
                        self.path,
                        content=body,
                        headers={
                            "X-PDRD-Review-Key": UI_KEY,
                            "Origin": base,
                            "Content-Type": "application/json",
                        },
                    )

            response = asyncio.run(request())
            self.send_bytes(response.status_code, response.content, response.headers)

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(Handler, directory=str(ROOT / "frontend/src")),
    )
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base + "/fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_pointer_geometry_survives_server_save_and_reload(
    review_browser_server, tmp_path
):
    """Мышь меняет обе области VLM/Gold; CAS-сохранение переживает перезагрузку и масштаб."""
    import playwright.sync_api as playwright

    with playwright.sync_playwright() as runtime:
        # launch() создаёт отдельный временный профиль; connect_over_cdp не используется.
        browser = runtime.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(review_browser_server, wait_until="networkidle")
            page.get_by_text(
                "Изменения сохранены на сервере и восстановятся после перезагрузки.",
                exact=False,
            ).wait_for(state="attached", timeout=10000)
            selectors = [
                ".analysis-result__bbox",
                ".analysis-result__annotation",
                ".manual-annotation__issue",
                ".manual-annotation__card",
            ]
            expected = []
            for selector in selectors:
                node = page.locator(selector).first
                node.scroll_into_view_if_needed()
                before = node.bounding_box()
                handle = node.locator('[data-resize-direction="se"]')
                rect = handle.bounding_box()
                page.mouse.move(
                    rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
                )
                page.mouse.down()
                page.mouse.move(
                    rect["x"] + rect["width"] / 2 + 25,
                    rect["y"] + rect["height"] / 2 + 20,
                    steps=6,
                )
                page.mouse.up()
                page.get_by_text(
                    "Изменения сохранены на сервере и восстановятся после перезагрузки.",
                    exact=False,
                ).wait_for(state="attached", timeout=10000)
                after = node.bounding_box()
                assert after["width"] > before["width"] + 15, (selector, before, after)
                assert after["height"] > before["height"] + 10, (
                    selector,
                    before,
                    after,
                )
                move = node.locator('[data-resize-direction="move"]')
                assert move.count() == 1
                rect = move.bounding_box()
                page.mouse.move(
                    rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
                )
                page.mouse.down()
                page.mouse.move(
                    rect["x"] + rect["width"] / 2 + 20,
                    rect["y"] + rect["height"] / 2 + 15,
                    steps=6,
                )
                page.mouse.up()
                page.get_by_text(
                    "Изменения сохранены на сервере и восстановятся после перезагрузки.",
                    exact=False,
                ).wait_for(state="attached", timeout=10000)
                moved = node.bounding_box()
                assert moved["x"] > after["x"] + 10 and moved["y"] > after["y"] + 5, (
                    selector,
                    after,
                    moved,
                )
                coordinates = node.evaluate(
                    "n=>[n.style.left,n.style.top,n.style.width,n.style.height]"
                )
                expected.append(
                    (selector, moved["width"], moved["height"], coordinates)
                )
            page.reload(wait_until="networkidle")
            page.get_by_text(
                "Изменения сохранены на сервере и восстановятся после перезагрузки.",
                exact=False,
            ).wait_for(state="attached", timeout=10000)
            for selector, width, height, coordinates in expected:
                actual = page.locator(selector).first.bounding_box()
                assert (
                    abs(actual["width"] - width) < 3
                    and abs(actual["height"] - height) < 3
                ), (selector, actual, width, height)
                assert (
                    page.locator(selector).first.evaluate(
                        "n=>[n.style.left,n.style.top,n.style.width,n.style.height]"
                    )
                    == coordinates
                )
            page.set_viewport_size({"width": 1000, "height": 900})
            page.evaluate(
                "()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
            )
            for selector, _width, _height, coordinates in expected:
                assert (
                    page.locator(selector).first.evaluate(
                        "n=>[n.style.left,n.style.top,n.style.width,n.style.height]"
                    )
                    == coordinates
                )
            page.screenshot(
                path=str(tmp_path / "review-geometry-restored.png"), full_page=True
            )
            assert not errors
        finally:
            browser.close()
