# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/normative_protocol.py

"""Атомарные архивы N-first рядом с request/result, в существующем persistent volume."""

import asyncio
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from pdrd_api_gateway.application.ports.normative_protocol import (
    NormativeProtocolError,
    NormativeProtocolNotFound,
)

PART_BYTES = 256 * 1024
READ_BYTES = 1024 * 1024


def _json(value):
    """Совпадает с каноническим форматом контракта Analysis Service."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _atomic(path, content):
    """Публикует только целый fsync-файл; временное имя никогда не является результатом."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _decode(value, nodes, depth=0, memo=None):
    """Восстанавливает точное значение; повреждённые/циклические ссылки закрываются ошибкой."""
    if depth > 100:
        raise NormativeProtocolError("Слишком глубокая ссылка протокола.")
    if not isinstance(value, dict) or set(value) != {"$ref"}:
        return value
    memo = {} if memo is None else memo
    identifier = value["$ref"]
    if identifier in memo:
        return memo[identifier]
    node = nodes[identifier]
    if node["kind"] == "text":
        return node["value"]
    if node["kind"] == "array":
        result = [_decode(child, nodes, depth + 1, memo) for child in node["value"]]
    elif node["kind"] == "object":
        result = {
            key: _decode(child, nodes, depth + 1, memo)
            for key, child in node["value"].items()
        }
    else:
        raise NormativeProtocolError("Неизвестный тип узла протокола.")
    memo[identifier] = result
    return result


def _diagnostic_plan(diagnostics, page_metrics):
    """Проецирует только поля счётчиков из каждого фактически сохранённого плана."""
    checks, legacy = [], []

    def add(plan):
        """Сохраняет digest observations, а не многократно развёрнутые исходные цитаты."""
        for row in plan.get("checks", []):
            checks.append(
                {
                    **{
                        key: row.get(key, "unknown")
                        for key in (
                            "requirement_id",
                            "result",
                            "rule",
                            "reason_code",
                            "currency",
                            "source_kind",
                            "applicability",
                        )
                    },
                    "observation_digest": hashlib.sha256(
                        json.dumps(row.get("observations"), sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
        legacy.extend(plan.get("legacy_records", []))

    add(diagnostics)
    for page in page_metrics:
        plan = page.get("metrics", {}).get("requirement_plan")
        if isinstance(plan, dict):
            add(plan)
    return {"checks": checks, "legacy_records": legacy}


class LocalNormativeProtocolStore:
    """Изоляция document_id, content-addressed identity и публикация без SQL-миграции."""

    def __init__(self, root_path: Path):
        """Получает тот же корень, что LocalFilesystemAnalysisArtifactStore."""
        absolute = str(root_path.resolve())
        # SHA-256 и UUID обязательны для идентичности; Windows не должен обрезать
        # их из-за исторического MAX_PATH при длинном каталоге quality gate.
        if os.name == "nt" and not absolute.startswith("\\\\?\\"):
            absolute = (
                "\\\\?\\UNC\\" + absolute[2:]
                if absolute.startswith("\\\\")
                else "\\\\?\\" + absolute
            )
        self.root_path = Path(absolute)

    def _directory(self, document_id, protocol_id):
        """Не допускает произвольные пути из HTTP."""
        if not re.fullmatch(r"[0-9a-f]{64}", protocol_id):
            raise NormativeProtocolError("Некорректный digest протокола.")
        directory = self.root_path / str(document_id)
        if not (directory / "request.json").is_file():
            raise NormativeProtocolNotFound("Документ не найден.")
        return directory / "normative-protocols" / protocol_id

    async def put_part(self, document_id, protocol_id, index, content):
        """Одна часть ограничена 256 KiB; несовпадающий повтор не перезаписывает доказательства."""
        try:
            await asyncio.to_thread(
                self._put_part, document_id, protocol_id, index, content
            )
        except OSError as error:
            raise NormativeProtocolError(
                "Не удалось сохранить часть протокола."
            ) from error

    def _put_part(self, document_id, protocol_id, index, content):
        """Выполняет файловые операции вне event loop."""
        if not 0 <= index < 100000 or not 0 < len(content) <= PART_BYTES:
            raise NormativeProtocolError("Некорректная часть протокола.")
        path = self._directory(document_id, protocol_id) / f"{index:06d}.part"
        if path.exists():
            if path.read_bytes() != content:
                raise NormativeProtocolError(
                    "Повторная часть отличается от сохранённой."
                )
            return
        _atomic(path, content)

    async def publish(self, document_id, protocol_id, manifest):
        """Публикует манифест только после проверки всех данных и scope архива."""
        return await asyncio.to_thread(
            self._publish, document_id, protocol_id, manifest
        )

    def _load_parts(self, directory, manifest):
        """Не считает отсутствующую/повреждённую часть пустым результатом."""
        parts = [
            directory / f"{index:06d}.part" for index in range(manifest["part_count"])
        ]
        raw = b"".join(part.read_bytes() for part in parts)
        if (
            len(raw) != manifest["bytes"]
            or hashlib.sha256(raw).hexdigest() != directory.name
        ):
            raise NormativeProtocolError("Нарушена целостность протокола.")
        return json.loads(raw)

    def _publish(self, document_id, protocol_id, manifest):
        """Проверяет digest, узлы, корни, счётчики и изоляцию перед атомарной публикацией."""
        try:
            directory = self._directory(document_id, protocol_id)
            archive = self._load_parts(directory, manifest)
            if archive["schema"] != "pdrd.normative-protocol/v1" or archive[
                "document_id"
            ] != str(document_id):
                raise NormativeProtocolError(
                    "Документ протокола не совпадает с заявкой."
                )
            for field in ("section_id", "document_ids"):
                if archive[field] != manifest[field]:
                    raise NormativeProtocolError("Нормативный scope протокола изменён.")
            nodes = {row["id"]: row for row in archive["nodes"]}
            if len(nodes) != len(archive["nodes"]):
                raise NormativeProtocolError("Повтор канонического ID.")
            for node in nodes.values():
                if (
                    hashlib.sha256(
                        _json({"kind": node["kind"], "value": node["value"]})
                    ).hexdigest()
                    != node["id"]
                ):
                    raise NormativeProtocolError(
                        "Digest канонического источника не совпадает."
                    )
                children = (
                    node["value"].values()
                    if node["kind"] == "object"
                    else node["value"]
                    if node["kind"] == "array"
                    else []
                )
                if any(
                    isinstance(child, dict) and child.get("$ref") not in nodes
                    for child in children
                ):
                    raise NormativeProtocolError("Не разрешена ссылка протокола.")
            # Полностью восстанавливаем на границе публикации, а не доверяем счётчикам клиента.
            memo = {}
            diagnostics = _decode(archive["roots"]["diagnostics"], nodes, memo=memo)
            source_rows = _decode(archive["roots"]["sources"], nodes, memo=memo)
            identities = set()
            generations = {
                row["document_id"]: row["generation_id"]
                for row in diagnostics.get("generations", [])
            }
            for source in source_rows:
                identity = tuple(
                    source[key]
                    for key in (
                        "document_id",
                        "generation_id",
                        "requirement_id",
                        "source_sha256",
                    )
                )
                if (
                    identity in identities
                    or source["document_id"] not in archive["document_ids"]
                    or generations.get(source["document_id"]) != source["generation_id"]
                ):
                    raise NormativeProtocolError(
                        "Неоднозначная идентичность нормативного источника."
                    )
                identities.add(identity)
            if len(diagnostics.get("selection", [])) != len(source_rows):
                raise NormativeProtocolError(
                    "Потеря исходных атомов или записей selection."
                )
            for name in ("checks", "selection", "sources", "stage_records"):
                count = len(_decode(archive["roots"][name], nodes, memo=memo))
                if (
                    count != archive["counts"][name]
                    or count != manifest["counts"][name]
                ):
                    raise NormativeProtocolError(
                        "Счётчики протокола не совпадают с данными."
                    )
            if len(diagnostics.get("checks", [])) != archive["counts"]["checks"]:
                raise NormativeProtocolError(
                    "Полный протокол проверок не совпадает с индексом."
                )
            if diagnostics.get("checks", []) != _decode(
                archive["roots"]["checks"], nodes, memo=memo
            ):
                raise NormativeProtocolError(
                    "Данные проверок отличаются от полного протокола."
                )
            projection = _json(
                _diagnostic_plan(
                    diagnostics, _decode(archive["roots"]["page_metrics"], nodes)
                )
            )
            _atomic(directory / "diagnostics.json", projection)
            stream_digest, stream_bytes = hashlib.sha256(), 0
            stream_rows = nodes[archive["roots"]["stage_records"]["$ref"]]["value"]
            temporary = directory / f".{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as target:
                    for row in stream_rows:
                        raw = _json(_decode(row, nodes)) + b"\n"
                        target.write(raw)
                        stream_digest.update(raw)
                        stream_bytes += len(raw)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, directory / "stage.jsonl")
            finally:
                temporary.unlink(missing_ok=True)
            published = {
                **manifest,
                "protocol_id": protocol_id,
                "document_id": str(document_id),
                "state": "ready",
                "diagnostics_digest": hashlib.sha256(projection).hexdigest(),
                "stage_stream": {
                    "bytes": stream_bytes,
                    "sha256": stream_digest.hexdigest(),
                },
            }
            target = directory / "manifest.json"
            if target.exists() and json.loads(target.read_bytes()) != published:
                raise NormativeProtocolError(
                    "Манифест опубликован с другим содержимым."
                )
            _atomic(target, _json(published))
            return published
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise NormativeProtocolError(
                "Не удалось проверить и опубликовать протокол."
            ) from error

    async def read(
        self, document_id, protocol_id, collection="manifest", offset=0, limit=100
    ):
        """Стабильная пагинация; byte parts позволяют получить даже один очень большой атом."""
        return await asyncio.to_thread(
            self._read, document_id, protocol_id, collection, offset, limit
        )

    def _read(self, document_id, protocol_id, collection, offset, limit):
        """Восстанавливает только запрошенную страницу; полный JSON не уходит клиенту."""
        directory = self._directory(document_id, protocol_id)
        try:
            manifest = json.loads((directory / "manifest.json").read_bytes())
        except FileNotFoundError as error:
            raise NormativeProtocolNotFound("Протокол не опубликован.") from error
        if collection == "manifest":
            return manifest
        if not offset >= 0 or not 1 <= limit <= 200:
            raise NormativeProtocolError("Некорректная пагинация.")
        try:
            archive = self._load_parts(directory, manifest)
            nodes = {row["id"]: row for row in archive["nodes"]}
            if collection == "nodes":
                rows = archive["nodes"]
            elif collection in {
                "checks",
                "selection",
                "sources",
                "page_metrics",
                "stage_records",
            }:
                rows = nodes[archive["roots"][collection]["$ref"]]["value"]
            else:
                raise NormativeProtocolError("Неизвестная коллекция протокола.")
            items, size, memo = [], 0, {}
            for value in rows[offset : offset + limit]:
                # Узлы доступны отдельно: даже огромная цитата не расширяет страницу checks.
                row = (
                    value if collection == "nodes" else _decode(value, nodes, memo=memo)
                )
                row_size = len(_json(row))
                if size + row_size > READ_BYTES:
                    if items:
                        break
                    # Ссылка сохраняет весь объект; исходные байты доступны через part API.
                    row = {"encoded": value, "oversized": True}
                    row_size = len(_json(row))
                    if row_size > READ_BYTES:
                        raise NormativeProtocolError(
                            "Большой узел доступен через byte parts протокола."
                        )
                items.append(row)
                size += row_size
            next_offset = offset + len(items)
            return {
                "protocol_id": protocol_id,
                "collection": collection,
                "offset": offset,
                "total": len(rows),
                "items": items,
                "next_offset": next_offset if next_offset < len(rows) else None,
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise NormativeProtocolError("Не удалось прочитать протокол.") from error

    async def read_part(self, document_id, protocol_id, index):
        """Читает ограниченные исходные байты только опубликованного архива."""
        manifest = await self.read(document_id, protocol_id)
        if not 0 <= index < manifest["part_count"]:
            raise NormativeProtocolNotFound("Часть не найдена.")
        path = self._directory(document_id, protocol_id) / f"{index:06d}.part"
        return await asyncio.to_thread(path.read_bytes)

    async def stream(self, document_id, protocol_id, offset):
        """Читает JSONL стадии по 256 KiB, не сериализуя полный результат в n8n HTTP."""

        def read():
            directory = self._directory(document_id, protocol_id)
            try:
                manifest = json.loads((directory / "manifest.json").read_bytes())
                stream = manifest["stage_stream"]
                path = directory / "stage.jsonl"
                total = stream["bytes"]
                if not 0 <= offset < total:
                    raise NormativeProtocolNotFound("Смещение потока не найдено.")
                with path.open("rb") as source:
                    if offset == 0:
                        digest = hashlib.sha256()
                        while part := source.read(PART_BYTES):
                            digest.update(part)
                        if (
                            source.tell() != total
                            or digest.hexdigest() != stream["sha256"]
                        ):
                            raise NormativeProtocolError(
                                "Digest потока результатов не совпадает."
                            )
                    source.seek(offset)
                    content = source.read(PART_BYTES)
                end = offset + len(content)
                return {
                    "protocol_id": protocol_id,
                    "offset": offset,
                    "total_bytes": total,
                    "base64": base64.b64encode(content).decode("ascii"),
                    "next_offset": end if end < total else None,
                }
            except FileNotFoundError as error:
                raise NormativeProtocolNotFound("Поток не опубликован.") from error

        return await asyncio.to_thread(read)

    async def verify(self, document_id, protocol_id):
        """Не принимает один manifest за доказательство существования всех сохранённых частей."""

        def check():
            directory = self._directory(document_id, protocol_id)
            try:
                manifest = json.loads((directory / "manifest.json").read_bytes())
                self._load_parts(directory, manifest)
                projection = (directory / "diagnostics.json").read_bytes()
                if (
                    hashlib.sha256(projection).hexdigest()
                    != manifest["diagnostics_digest"]
                ):
                    raise NormativeProtocolError(
                        "Нарушен digest индекса диагностических счётчиков."
                    )
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise NormativeProtocolError(
                    "Полный нормативный протокол недоступен или повреждён."
                ) from error

        await asyncio.to_thread(check)

    async def diagnostic_plan(self, document_id, protocol_id):
        """Читает проверяемую проекцию счётчиков без полного раскрытия normative proof records."""

        def read():
            directory = self._directory(document_id, protocol_id)
            try:
                manifest = json.loads((directory / "manifest.json").read_bytes())
                raw = (directory / "diagnostics.json").read_bytes()
                if hashlib.sha256(raw).hexdigest() != manifest["diagnostics_digest"]:
                    raise NormativeProtocolError(
                        "Нарушен digest индекса диагностических счётчиков."
                    )
                return json.loads(raw)
            except FileNotFoundError as error:
                raise NormativeProtocolNotFound(
                    "Индекс диагностики не опубликован."
                ) from error

        return await asyncio.to_thread(read)
