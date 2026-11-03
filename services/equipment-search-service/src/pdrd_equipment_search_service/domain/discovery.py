# services/equipment-search-service/src/pdrd_equipment_search_service/domain/discovery.py

"""Ограниченное статическое обнаружение ссылок и предметные поисковые запросы."""

import re
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    SearchHit,
    safe_search_term,
)

_DOCUMENT = re.compile(
    r"паспорт|руководств|документац|каталог|сертификат|техническ|скачать|datasheet|download|documentation|manual|specification|\.pdf(?:\?|$)",
    re.I,
)


def public_url(url: str) -> str:
    """Исключает секретные параметры из диагностики и пользовательского отчёта."""
    if not SearchHit(url).hostname:
        return ""
    parsed = urlsplit(url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query)
            if not re.search(
                r"token|secret|auth|key|signature|password|session", key, re.I
            )
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))[:2000]


def candidate_id(url: str) -> str:
    """Возвращает устойчивый ID без сохранения секретов URL в открытом журнале."""
    return "EQC-" + sha256(public_url(url).encode()).hexdigest()[:24]


def search_queries(
    identity: EquipmentIdentity, hostname: str | None = None
) -> tuple[str, ...]:
    """Различает паспорт серийной модели, стандарт материала и проектную конструкцию."""
    base = " ".join(
        filter(
            None,
            (
                safe_search_term(identity.manufacturer),
                safe_search_term(identity.model),
                safe_search_term(identity.article),
                safe_search_term(identity.variant)
                if identity.variant_kind != "interface"
                else "",
            ),
        )
    )
    reference = safe_search_term(identity.reference)
    if identity.item_type == "serial_equipment":
        suffixes = (
            ("паспорт PDF", "технические характеристики", "руководство изготовитель")
            if re.search(r"[а-яё]", base, re.I)
            else ("datasheet PDF", "technical specifications", "manual manufacturer")
        )
    elif identity.item_type == "material":
        suffixes = (
            f"{reference} стандарт требования",
            "технические условия качество",
            "документы на поставку характеристики",
        )
    elif identity.item_type == "designed_structure":
        suffixes = (
            "расчёт проектирование нормы",
            "типовые решения чертежи",
            "технические требования объём",
        )
    elif identity.item_type == "prefabricated_structure":
        suffixes = (
            f"{reference} серия альбом",
            "каталог размеры чертежи",
            "технические условия",
        )
    elif identity.item_type == "standard_product":
        suffixes = (
            f"{reference} технические характеристики",
            "стандарт размеры давление",
            "каталог технический паспорт PDF",
        )
    else:
        suffixes = (
            "идентификация маркировка",
            "техническая документация",
            "каталог изделий",
        )
    host = f" site:{hostname}" if hostname else ""
    return tuple(f"{base} {suffix}{host}".strip() for suffix in suffixes)


class _Links(HTMLParser):
    """Читает только статические href, пропуская активное содержимое."""

    def __init__(self):
        """Ограничивает память числом исходных ссылок."""
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current = None
        self.ignored = 0
        self.context = ""
        self.row_start = None

    def handle_starttag(self, tag, attrs):
        """Сохраняет href и доступную подпись ссылки."""
        if tag in {"script", "style", "noscript", "iframe", "svg"}:
            self.ignored += 1
        if not self.ignored and tag == "tr":
            self.context = ""
            self.row_start = len(self.rows)
        if not self.ignored and tag == "a" and len(self.rows) < 1024:
            self.current = [dict(attrs).get("href", ""), "", self.context[-350:]]

    def handle_endtag(self, tag):
        """Завершает текущую ссылку без исполнения вложенного HTML."""
        if tag == "a" and self.current is not None:
            self.rows.append(self.current)
            self.current = None
        if not self.ignored and tag == "tr" and self.row_start is not None:
            for row in self.rows[self.row_start :]:
                row[2] = self.context[-350:]
            self.row_start = None
        if tag in {"script", "style", "noscript", "iframe", "svg"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        """Читает только ограниченную подпись."""
        if self.current is not None and not self.ignored:
            self.current[1] = (self.current[1] + data)[:240]
        if not self.ignored:
            self.context = (self.context + " " + data.strip())[-350:]


def discover_links(
    content: bytes,
    base_url: str,
    limit: int = 8,
    *,
    identity: EquipmentIdentity | None = None,
) -> tuple[SearchHit, ...]:
    """Нормализует относительные URL; разрешение hostname проверяется перед загрузкой."""
    parser = _Links()
    head = content[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.I)
    encoding = match.group(1).lower() if match else "utf-8"
    if encoding not in {"utf-8", "windows-1251", "cp1251", "koi8-r"}:
        encoding = "utf-8"
    parser.feed(content[:2_000_000].decode(encoding, errors="replace"))
    result, seen = [], {base_url}
    for href, label, context in parser.rows:
        title = (context + " " + label).strip()[-350:]
        url = urljoin(base_url, href).split("#", 1)[0]
        if (
            url in seen
            or not SearchHit(url).hostname
            or not _DOCUMENT.search(label + " " + unquote(url))
        ):
            continue
        # HTTPS — отдельная безопасная попытка; разрешение HTTP не включается.
        if urlsplit(url).scheme == "http":
            url = "https:" + url[5:]
        if url in seen:
            continue
        seen.add(url)
        result.append(SearchHit(url, title))
    marker = (
        re.match(r"[a-zа-яё]+", identity.model, re.I).group()
        if identity and re.match(r"[a-zа-яё]+", identity.model, re.I)
        else ""
    )
    result.sort(key=lambda hit: _link_rank(hit, identity, marker))
    return tuple(result[:limit])


def _link_rank(
    hit: SearchHit, identity: EquipmentIdentity | None, marker: str
) -> tuple:
    """Ранжирует ссылки; диапазон DN служит подсказкой, а не доказательством применимости."""
    text = unquote(hit.url + " " + hit.title).casefold()
    pdf = bool(re.search(r"\.pdf(?:\?|$)", hit.url, re.I))
    archive = bool(re.search(r"\.(?:rar|zip|7z|exe|msi)(?:\?|$)", hit.url, re.I))
    numbers = re.findall(r"\d+", identity.model) if identity else []
    size = int(numbers[-1]) if numbers else None
    ranges = re.findall(r"dn\s*(\d+)\s*[-–]\s*(\d+)", text)
    compatible = bool(
        size is not None and any(int(low) <= size <= int(high) for low, high in ranges)
    )
    return (
        archive,
        not pdf,
        not bool(marker and marker.casefold() in text),
        not compatible,
        hit.url,
    )


def html_access_issue(content: bytes) -> str:
    """Явный challenge диагностируется без исполнения JavaScript или обхода CAPTCHA."""
    text = content[:200_000].decode("utf-8", errors="ignore")
    if re.search(
        r"enable(?:d)?\s+JavaScript|включите\s+JavaScript|enable\s+javascript",
        text,
        re.I,
    ):
        return "javascript_required"
    if re.search(
        r"<title>[^<]*(?:captcha|access denied|доступ запрещён)|verify you are human",
        text,
        re.I,
    ):
        return "access_challenge"
    return ""
