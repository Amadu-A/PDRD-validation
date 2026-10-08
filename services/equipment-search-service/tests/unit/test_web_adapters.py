# services/equipment-search-service/tests/unit/test_web_adapters.py

"""Проверки JSON поиска и запрета SSRF при загрузке документации."""

import socket

import httpx
import pytest
from pdrd_equipment_search_service.infrastructure import safe_download
from pdrd_equipment_search_service.infrastructure.searxng import SearxngSearch


@pytest.mark.asyncio
async def test_searxng_parses_only_valid_unique_urls() -> None:
    """Snippet не используется; повторные и внутренние ссылки исключаются."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://maker.example/manual.pdf",
                        "title": "Manual",
                        "content": "Не доказательство",
                    },
                    {"url": "https://maker.example/manual.pdf", "title": "Duplicate"},
                    {"url": "file:///etc/passwd", "title": "Bad"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hits = await SearxngSearch(client, "http://searxng:8080").search(
            "MEAN WELL DRC-100B datasheet",
            5,
        )
    assert len(hits) == 1
    assert hits[0].url == "https://maker.example/manual.pdf"
    assert requests[0].url.params["format"] == "json"
    assert requests[0].url.params["q"] == "MEAN WELL DRC-100B datasheet"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "192.168.1.1",
        "::1",
        "fc00::1",
    ],
)
def test_private_dns_answers_are_rejected(monkeypatch, address: str) -> None:
    """Любой внутренний DNS-ответ запрещает запрос до подключения."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))
        ],
    )
    with pytest.raises(safe_download.UnsafeDocumentUrlError):
        safe_download._target("https://maker.example/manual.pdf", allow_http=False)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://maker.example/manual.pdf",
        "https://user:pass@maker.example/manual.pdf",
        "https://maker.example:99999/manual.pdf",
    ],
)
def test_unsafe_url_is_rejected_before_network(monkeypatch, url: str) -> None:
    """Схема, credentials и порт проверяются до DNS запроса."""
    monkeypatch.setattr(
        safe_download,
        "_public_address",
        lambda host, port: "8.8.8.8",
    )
    with pytest.raises(safe_download.UnsafeDocumentUrlError):
        safe_download._target(url, allow_http=False)


def test_actual_media_type_ignores_server_label() -> None:
    """Загрузчик принимает только настоящий PDF или HTML."""
    assert safe_download._detect_media_type(b"%PDF-1.7") == "application/pdf"
    assert safe_download._detect_media_type(b"<!DOCTYPE html><html>") == "text/html"
    with pytest.raises(safe_download.InvalidDocumentResponseError):
        safe_download._detect_media_type(b"not a document")


def test_cancelled_download_stops_before_dns(monkeypatch) -> None:
    """Отменённый поток не запускает DNS и следующий HTTP запрос."""
    from threading import Event

    cancelled = Event()
    cancelled.set()

    def unexpected(*args, **kwargs):
        """Любое разрешение адреса после отмены является ошибкой."""
        raise AssertionError("DNS после отмены")

    monkeypatch.setattr(safe_download, "_target", unexpected)
    with pytest.raises(safe_download.InvalidDocumentResponseError, match="отменена"):
        safe_download.SafeDocumentDownloader()._download_sync(
            "https://maker.example/manual.pdf", allow_http=False, cancel_event=cancelled
        )


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch) -> None:
    """Публичный первый адрес не скрывает внутренний второй DNS ответ."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(safe_download.UnsafeDocumentUrlError):
        safe_download._target("https://maker.example/manual.pdf", allow_http=False)
