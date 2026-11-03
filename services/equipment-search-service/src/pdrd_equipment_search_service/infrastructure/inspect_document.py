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
    SearchHit,
    SourceStatus,
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
        if identity.item_type != "serial_equipment":
            kind_terms = {
                "material": r"песок|бетон|щебень|арматур",
                "standard_product": r"труб|отвод|люк|фитинг|фланец|тройник|втулк|задвижк|гильз",
                "prefabricated_structure": r"колод|кольц|железобетон",
                "designed_structure": r"резервуар|упор",
            }
            # Совпадение стандарта и предмета даёт справочник, а не паспорт поставки.
            relevant = tuple(
                (page, content)
                for page, content in pages
                if (
                    not identity.reference
                    or _contains_identifier(content, identity.reference)
                )
                and re.search(
                    kind_terms.get(identity.item_type, r"(?!)"), content, re.I
                )
            )
            return DocumentInspection(
                bool(relevant),
                pages=pages,
                applicability="technical_reference"
                if relevant
                else "insufficient_evidence",
                document_type="technical_reference",
                vision_pages=tuple(page for page, _ in relevant),
                reason_code="" if relevant else "reference_not_applicable",
                reason="Справочный документ не подтверждает изготовителя и конкретную поставку.",
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
            family = re.match(r"[A-Za-zА-Яа-яЁё]+(?:[\s-]+\d+)?", identity.model)
            family_pages = tuple(
                page
                for page, content in pages
                if family and _contains_identifier(content, family.group())
            )
            return DocumentInspection(
                False,
                reason="Модель не найдена в документе.",
                pages=pages,
                reason_code="page_coverage_incomplete"
                if int(payload.get("total_pages") or len(pages)) > len(pages)
                else ReasonCode.MODEL_MISMATCH,
                applicability="partial_match"
                if int(payload.get("total_pages") or len(pages)) > len(pages)
                else "family_match"
                if family_pages
                else "wrong_model",
                vision_pages=family_pages,
            )
        manufacturer = identity.manufacturer
        profile_reader = getattr(self.catalog, "manufacturer_profiles", None)
        profiles = await profile_reader() if profile_reader is not None else ()
        page_text = " ".join(model_pages)
        maker_evidence = ""
        raw_labels = re.findall(
            r"(?:изготовитель|производитель|manufacturer|manufactured by)\s*[:—-]\s*([^\n|;]{2,120})",
            "\n".join(model_pages),
            re.I,
        )
        raw_labels = [
            label.strip().strip('"')
            for label in raw_labels
            if not re.search(r"https?://|не\s+указан|unknown|продавец", label, re.I)
        ]
        maker_page = 0
        # В паспорте подпись изготовителя часто находится в разделе гарантий,
        # отдельно от страницы точной модели. Общий каталог этим правилом не связывается.
        frontmatter = "\n".join(content for _, content in pages[:2])[:12000]
        if (
            not raw_labels
            and document.media_type == "application/pdf"
            and re.search(
                r"руководство\s+по\s+эксплуатации|паспорт|operating\s+manual|datasheet",
                frontmatter,
                re.I,
            )
            and not re.search(
                r"каталог\s+(?:продукции|изделий)|product\s+catalog", frontmatter, re.I
            )
        ):
            labels = [
                (page, match.group(1).strip().rstrip(" ."))
                for page, content in pages
                for match in re.finditer(
                    r"(?:изготовитель|производитель|manufacturer|manufactured by)\s*[:—-]\s*([^\n|;]{2,120})",
                    content,
                    re.I,
                )
            ]
            labels = [
                (page, label)
                for page, label in labels
                if not re.search(r"https?://|не\s+указан|unknown|продавец", label, re.I)
            ]
            if len({label.casefold() for _, label in labels}) == 1:
                maker_page, label = labels[0]
                raw_labels = [label]
                page_text += "\nИзготовитель: " + label
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
                _contains_identifier(page_text, alias) for alias in aliases if alias
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
            if not candidates and len({label.casefold() for label in raw_labels}) == 1:
                manufacturer = raw_labels[0]
                maker_evidence = "Изготовитель: " + raw_labels[0]
                verified = True
        if (
            identity.manufacturer
            and raw_labels
            and not any(
                _contains_identifier(label, alias)
                for label in raw_labels
                for alias in aliases
                if alias
            )
        ):
            return DocumentInspection(
                False,
                pages=pages,
                reason="Явная подпись изготовителя противоречит проектной идентичности.",
                reason_code=ReasonCode.MANUFACTURER_MISMATCH,
                applicability="wrong_manufacturer",
            )
        if not verified:
            # Известный точный сайт производителя тоже является доказательством
            # происхождения. Чужая явная подпись изготовителя имеет приоритет.
            reader = getattr(self.catalog, "source_for", None)
            source = (
                await reader(
                    identity.manufacturer, SearchHit(document.final_url).hostname
                )
                if reader is not None and identity.manufacturer
                else None
            )
            labels = re.findall(
                r"(?:изготовитель|производитель|manufacturer|manufactured by)\s*[:—-]\s*([^\n|;]{1,120})",
                page_text,
                re.I,
            )
            conflicting_label = (
                any(
                    not any(
                        _contains_identifier(label, alias) for alias in aliases if alias
                    )
                    for label in labels
                )
                if identity.manufacturer
                else True
            )
            verified = bool(
                source is not None
                and source.status is SourceStatus.TRUSTED
                and source.matches(
                    identity.manufacturer, SearchHit(document.final_url).hostname
                )
                and not conflicting_label
                and (
                    not observed
                    or (
                        len(labelled or observed) == 1
                        and bool((labelled or observed) & own)
                    )
                )
            )
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
                requires_vision=not identity.manufacturer
                and document.media_type == "application/pdf",
                vision_pages=vision_pages,
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
            vision_pages=vision_pages,
            manufacturer_evidence=maker_evidence,
            manufacturer_evidence_page=maker_page,
            document_type="product_page"
            if document.media_type == "text/html"
            else "technical_document",
        )
