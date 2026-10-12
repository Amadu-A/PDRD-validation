# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/inspect_document.py

"""Проверка модели через текст, извлечённый существующим Document Service."""

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

import httpx

from pdrd_equipment_search_service.application.ports import EquipmentCatalog
from pdrd_equipment_search_service.domain.diagnostics import ReasonCode
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
)


def _identifier_pattern(identifier: str) -> str:
    """Формирует точный шаблон маркировки с допустимой типографикой."""
    normalized = re.sub(r"\bисполнение\s*", "исп. ", identifier.strip().casefold())
    pieces = re.split(r"[\s\-–—−]+", normalized)
    needle = r"[\s\-–—−]*".join(
        r"\s*".join(re.escape(char) for char in part) for part in pieces
    )
    if normalized.startswith("исп."):
        needle = needle.replace(r"и\s*с\s*п\s*\.", r"и\s*с\s*п\s*\.?")
    return rf"(?<![\w\-–—−]){needle}(?![\w\-–—−])"


def _clean_text(text: str) -> str:
    """Сохраняет строки и устраняет перенос слова и длинную запись исполнения."""
    text = re.sub(r"(?<=[^\W\d_])[-–—]\s*\n\s*(?=[^\W\d_])", "", text)
    return re.sub(r"\bисполнение\s*", "исп. ", text.casefold())


def _contains_identifier(text: str, identifier: str) -> bool:
    """Ищет маркировку без совпадения с соседней моделью или исполнением."""
    return (
        not identifier.strip()
        or re.search(_identifier_pattern(identifier), _clean_text(text)) is not None
    )


def _model_contexts(text: str, identity: EquipmentIdentity) -> tuple[str, ...]:
    """Не заимствует исполнение и артикул из строки другой модели на том же листе."""
    text = _clean_text(text)
    targets = list(re.finditer(_identifier_pattern(identity.model), text))
    generic = re.finditer(
        r"(?<!\w)(?:[a-zа-яё]{1,5}\s+\d+(?:[-–—]\w+)+|"
        r"[a-zа-яё]\w*[-–—]\s*\w*\d\w*(?:[-–—]\w+)*|"
        r"[a-zа-яё]{1,10}\d\w*)(?!\w)",
        text,
    )
    markers = [
        (match.start(), match.end())
        for match in generic
        if not any(
            match.start() < target.end() and match.end() > target.start()
            for target in targets
        )
        and not (
            identity.variant and _contains_identifier(match.group(), identity.variant)
        )
        and not (
            identity.article and _contains_identifier(match.group(), identity.article)
        )
    ]
    markers.extend((match.start(), match.end()) for match in targets)
    separators = list(re.finditer(r";|\n\s*\n", text))
    contexts = []
    for target in targets:
        start = max(
            (
                separator.end()
                for separator in separators
                if separator.end() <= target.start()
            ),
            default=0,
        )
        if any(left >= start and right <= target.start() for left, right in markers):
            start = target.start()
        end = min(
            (left for left, _ in markers if left >= target.end()), default=len(text)
        )
        end = min(
            end,
            min(
                (
                    separator.start()
                    for separator in separators
                    if separator.start() >= target.end()
                ),
                default=len(text),
            ),
        )
        contexts.append(text[start:end])
    return tuple(contexts)


@dataclass(slots=True)
class DocumentServiceInspector:
    """Использует выделенный text-only контракт Document Service."""

    client: httpx.AsyncClient
    base_url: str
    timeout_seconds: float = 30.0
    catalog: EquipmentCatalog | None = None

    async def inspect(
        self,
        identity: EquipmentIdentity,
        document: DownloadedDocument,
    ) -> DocumentInspection:
        """Отклоняет документ без производителя, модели или исполнения."""
        response = await self.client.post(
            f"{self.base_url.rstrip('/')}/internal/v1/equipment-documents/extract",
            files={"file": ("documentation", document.content, document.media_type)},
            data={"media_type": document.media_type},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        pages = tuple(
            (int(page["page"]), str(page["text"]))
            for page in payload.get("pages", [])
            if isinstance(page, dict)
        )
        text = " ".join(page_text for _, page_text in pages)
        if document.media_type == "application/pdf" and not text.strip():
            return DocumentInspection(
                False,
                reason="Сканированный PDF требует адресного визуального извлечения.",
                pages=pages,
                requires_vision=True,
                reason_code="scan_requires_vision",
            )
        model_pages = tuple(
            page_text
            for _, page_text in pages
            if _contains_identifier(page_text, identity.model)
        )
        contexts = tuple(
            context
            for page_text in model_pages
            for context in _model_contexts(page_text, identity)
        )
        vision_pages = tuple(
            page
            for page, page_text in pages
            if _contains_identifier(page_text, identity.model)
        )
        if not model_pages:
            return DocumentInspection(
                False,
                reason="Модель не найдена в документе.",
                pages=pages,
                reason_code=ReasonCode.MODEL_MISMATCH,
            )
        manufacturer = identity.manufacturer
        profile_reader = getattr(self.catalog, "manufacturer_profiles", None)
        profiles = await profile_reader() if profile_reader is not None else ()
        page_text = " ".join(model_pages)
        observed = {
            profile["name"]
            for profile in profiles
            if any(
                _contains_identifier(page_text, alias)
                for alias in (profile["name"], *profile.get("aliases", ()))
                if alias
            )
        }
        labelled = {
            profile["name"]
            for profile in profiles
            if any(
                re.search(
                    r"(?:производитель|изготовитель|manufacturer|manufactured by)"
                    r"\s*[:—-]?\s*(?:ооо\s*[«\"]?)?" + _identifier_pattern(alias),
                    _clean_text(page_text),
                )
                for alias in (profile["name"], *profile.get("aliases", ()))
                if alias
            )
        }
        if manufacturer:
            aliases = (manufacturer,)
            if self.catalog is not None:
                aliases = await self.catalog.manufacturer_aliases(manufacturer)
            verified = any(
                _contains_identifier(" ".join(model_pages), alias)
                for alias in aliases
                if alias
            )
            if observed:
                own = {
                    profile["name"]
                    for profile in profiles
                    if any(
                        _contains_identifier(alias, known)
                        for known in (profile["name"], *profile.get("aliases", ()))
                        for alias in aliases
                    )
                }
                candidates = labelled or observed
                verified = verified and len(candidates) == 1 and bool(candidates & own)
        else:
            candidates = labelled or observed
            verified = len(candidates) == 1
            manufacturer = next(iter(candidates)) if verified else ""
        if not verified:
            return DocumentInspection(
                False,
                reason="Производитель не подтверждён текстом документа.",
                pages=pages,
                reason_code=(
                    ReasonCode.MANUFACTURER_MISMATCH
                    if identity.manufacturer
                    else ReasonCode.MANUFACTURER_UNKNOWN
                ),
            )
        if (
            identity.variant
            and identity.variant_kind != "interface"
            and not any(
                _contains_identifier(context, identity.variant) for context in contexts
            )
        ):
            return DocumentInspection(
                False,
                reason="Исполнение не подтверждено документом.",
                pages=pages,
                reason_code=ReasonCode.VARIANT_MISMATCH,
                requires_vision=document.media_type == "application/pdf"
                and ("таблиц" in text.casefold() or "\t" in text),
                vision_pages=vision_pages,
            )
        revision = ""
        if identity.article and not any(
            _contains_identifier(context, identity.article)
            and (
                not identity.variant
                or identity.variant_kind == "interface"
                or _contains_identifier(context, identity.variant)
            )
            for context in contexts
        ):
            return DocumentInspection(
                False,
                reason="Артикул не подтверждён документом.",
                pages=pages,
                reason_code=ReasonCode.ARTICLE_MISMATCH,
                vision_pages=vision_pages,
            )
        match = re.search(
            r"(?:rev(?:ision)?[.\s:]*|версия[.\s:]*)([\w.\-]{1,24})",
            text[:8000],
            re.IGNORECASE,
        )
        if match:
            revision = match.group(1)
        publication_date = ""
        date_match = re.search(
            r"(?:publication\s+date|issue\s+date|дата\s+(?:выпуска|издания))"
            r"\s*[:№-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})",
            text[:8000],
            re.IGNORECASE,
        )
        if date_match:
            raw_date = date_match.group(1)
            date_format = "%Y-%m-%d" if "-" in raw_date else "%d.%m.%Y"
            with suppress(ValueError):
                publication_date = (
                    datetime.strptime(raw_date, date_format).date().isoformat()
                )
        return DocumentInspection(
            True,
            revision=revision,
            publication_date=publication_date,
            pages=pages,
            manufacturer=manufacturer,
        )
