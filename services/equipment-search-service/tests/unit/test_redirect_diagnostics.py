# services/equipment-search-service/tests/unit/test_redirect_diagnostics.py

"""Разрешённый CDN не отключает TLS, закрепление IP и защиту SSRF."""

from types import SimpleNamespace

import pytest
from pdrd_equipment_search_service.infrastructure import safe_download


def transport(monkeypatch, responses):
    """Подменяет только сокетный транспорт, сохраняя реальную проверку каждого адреса."""
    requested = []

    class Connection:
        """Запоминает hostname и возвращает контролируемый HTTP-ответ."""

        def __init__(self, hostname, port, address, timeout):
            self.hostname = hostname

        def request(self, method, path, headers):
            requested.append(self.hostname)

        def getresponse(self):
            status, headers, content = responses[self.hostname]
            parts = iter((content, b""))
            return SimpleNamespace(
                status=status, getheader=headers.get, read=lambda size: next(parts)
            )

        def close(self):
            """Завершает тестовое соединение."""

    monkeypatch.setattr(safe_download, "_PinnedHttpsConnection", Connection)
    monkeypatch.setattr(
        safe_download, "_public_address", lambda hostname, port: "8.8.8.8"
    )
    return requested


@pytest.mark.parametrize("approved", [False, True])
def test_cross_host_redirect_requires_explicit_exact_trusted_cdn(monkeypatch, approved):
    """Утверждённый точный CDN работает; по умолчанию переход запрещён до второго GET."""
    requested = transport(
        monkeypatch,
        {
            "maker.example": (302, {"Location": "https://cdn.example/manual.pdf"}, b""),
            "cdn.example": (200, {}, b"%PDF-1.7"),
        },
    )
    download = safe_download.SafeDocumentDownloader()
    if approved:
        result = download._download_sync(
            "https://maker.example/manual.pdf",
            allow_http=False,
            allowed_redirect_hosts=("cdn.example",),
        )
        assert result.final_url == "https://cdn.example/manual.pdf"
        assert requested == ["maker.example", "cdn.example"]
    else:
        with pytest.raises(safe_download.UnsafeDocumentUrlError) as error:
            download._download_sync(
                "https://maker.example/manual.pdf", allow_http=False
            )
        assert error.value.code == "redirect_blocked"
        assert requested == ["maker.example"]


@pytest.mark.parametrize(
    "target",
    [
        "https://nested.cdn.example/manual.pdf",
        "http://cdn.example/manual.pdf",
        "https://127.0.0.1/manual.pdf",
    ],
)
def test_cdn_permission_does_not_cover_subdomains_downgrade_or_private_ip(
    monkeypatch, target
):
    """Разрешение hostname не распространяется на соседние домены, HTTP и внутренние IP."""
    requested = transport(
        monkeypatch, {"maker.example": (302, {"Location": target}, b"")}
    )
    with pytest.raises(safe_download.UnsafeDocumentUrlError):
        safe_download.SafeDocumentDownloader()._download_sync(
            "https://maker.example/manual.pdf",
            allow_http=False,
            allowed_redirect_hosts=("cdn.example",),
        )
    assert requested == ["maker.example"]


@pytest.mark.parametrize(
    ("status", "headers", "content", "code"),
    [
        (404, {}, b"", "http_error"),
        (200, {"Content-Length": "5000"}, b"", "file_too_large"),
        (200, {}, b"%PDF-" + b"x" * 200, "file_too_large"),
        (200, {}, b"not PDF", "unsupported_file"),
    ],
)
def test_download_failure_has_safe_diagnostic_code(
    monkeypatch, status, headers, content, code
):
    """HTTP, лимит и формат имеют разные причины без выдачи URL credentials."""
    transport(monkeypatch, {"maker.example": (status, headers, content)})
    with pytest.raises(safe_download.InvalidDocumentResponseError) as error:
        safe_download.SafeDocumentDownloader(max_bytes=100)._download_sync(
            "https://maker.example/manual.pdf", allow_http=False
        )
    assert error.value.code == code
