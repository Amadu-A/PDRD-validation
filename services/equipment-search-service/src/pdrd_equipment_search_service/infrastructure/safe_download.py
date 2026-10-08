# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/safe_download.py

"""Безопасная загрузка внешней документации с закреплением проверенного IP."""

import asyncio
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from threading import Event
from urllib.parse import urljoin, urlsplit

from pdrd_equipment_search_service.domain.equipment import DownloadedDocument


class UnsafeDocumentUrlError(ValueError):
    """URL ведёт во внутреннюю сеть или нарушает политику источника."""


class InvalidDocumentResponseError(ValueError):
    """Ответ не является допустимым PDF/HTML документом."""


class _PinnedHttpConnection(http.client.HTTPConnection):
    """Подключается только к уже проверенному IP, сохраняя исходный Host."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        """Сохраняет проверенный адрес для TCP подключения."""
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        """Открывает TCP-соединение к закреплённому публичному IP."""
        self.sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
        )


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    """Проверяет TLS исходного hostname при подключении к закреплённому IP."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        """Создаёт стандартный проверяющий TLS-контекст."""
        super().__init__(
            host,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._address = address

    def connect(self) -> None:
        """Подключается к закреплённому IP с проверкой сертификата hostname."""
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _public_address(hostname: str, port: int) -> str:
    """Отклоняет адрес, если любой DNS-ответ не является публичным."""
    try:
        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise UnsafeDocumentUrlError("Не удалось проверить DNS источника.") from error
    addresses = tuple(dict.fromkeys(row[4][0] for row in answers))
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise UnsafeDocumentUrlError("Источник указывает на непубличный IP.")
    return addresses[0]


def _target(url: str, *, allow_http: bool) -> tuple[str, str, int, str]:
    """Проверяет схему, credentials, порт и DNS перед запросом."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise UnsafeDocumentUrlError("Некорректный URL документа.") from error
    if (
        parsed.scheme not in {"https", "http"}
        or (parsed.scheme == "http" and not allow_http)
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not 1 <= port <= 65535
    ):
        raise UnsafeDocumentUrlError("Недопустимый URL документа.")
    address = _public_address(hostname, port)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return hostname, address, port, path


@dataclass(frozen=True, slots=True)
class SafeDocumentDownloader:
    """Загружает PDF/HTML с лимитом байтов и контролем каждого redirect."""

    max_bytes: int = 12_000_000
    max_redirects: int = 4
    timeout_seconds: float = 10.0

    async def download(
        self, url: str, *, allow_http: bool = False
    ) -> DownloadedDocument:
        """Запускает блокирующий сетевой код вне event loop."""
        cancel_event = Event()
        try:
            return await asyncio.to_thread(
                self._download_sync,
                url,
                allow_http=allow_http,
                cancel_event=cancel_event,
            )
        finally:
            cancel_event.set()

    def _download_sync(
        self,
        url: str,
        *,
        allow_http: bool,
        cancel_event: Event | None = None,
    ) -> DownloadedDocument:
        """Проверяет каждый переход, закрепляет IP и читает ограниченное тело."""
        original_host = urlsplit(url).hostname
        current = url
        for redirect in range(self.max_redirects + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise InvalidDocumentResponseError("Загрузка отменена.")
            hostname, address, port, path = _target(
                current,
                allow_http=allow_http,
            )
            if hostname != original_host:
                raise UnsafeDocumentUrlError(
                    "Redirect на другой hostname требует отдельного разрешения."
                )
            connection_class = (
                _PinnedHttpsConnection
                if urlsplit(current).scheme == "https"
                else _PinnedHttpConnection
            )
            connection = connection_class(
                hostname,
                port,
                address,
                self.timeout_seconds,
            )
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Accept": "application/pdf,text/html",
                        "User-Agent": "PDRD-Equipment-Search/1.0",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirect >= self.max_redirects:
                        raise InvalidDocumentResponseError(
                            "Недопустимая цепочка redirect."
                        )
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise InvalidDocumentResponseError(
                        f"Источник вернул HTTP {response.status}."
                    )
                declared = response.getheader("Content-Length")
                if declared and int(declared) > self.max_bytes:
                    raise InvalidDocumentResponseError(
                        "Документ превышает допустимый размер."
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InvalidDocumentResponseError("Загрузка отменена.")
                    chunk = response.read(min(65536, self.max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise InvalidDocumentResponseError(
                            "Документ превышает допустимый размер."
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                media_type = _detect_media_type(content)
                return DownloadedDocument(content, current, media_type)
            finally:
                connection.close()
        raise InvalidDocumentResponseError("Слишком много redirect.")


def _detect_media_type(content: bytes) -> str:
    """Определяет фактический формат, игнорируя Content-Type сервера."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    prefix = content[:2048].lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        return "text/html"
    raise InvalidDocumentResponseError("Ответ не является PDF или HTML документом.")
