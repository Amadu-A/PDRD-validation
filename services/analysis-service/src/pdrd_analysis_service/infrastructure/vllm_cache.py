# services/analysis-service/src/pdrd_analysis_service/infrastructure/vllm_cache.py

"""Persistent exact-request cache для structured VLM."""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)

logger = logging.getLogger(
    "uvicorn.error",
)

_CACHE_RECORD_VERSION = 1

_CACHE_KEY_VERSION = 1

_CACHE_PRUNE_INTERVAL_SECONDS = 3600.0


@dataclass(slots=True)
class _SingleFlightEntry:
    """Состояние одного in-process exact-request single-flight."""

    lock: asyncio.Lock

    users: int = 0


class CachedStructuredVisionModel:
    """Декоратор VLM с persistent exact-request cache и single-flight."""

    def __init__(
        self,
        *,
        delegate: StructuredVisionModel,
        provider_identity: str,
        root_path: Path,
        namespace: str,
        ttl_seconds: int,
        enabled: bool,
    ) -> None:
        """Сохраняет delegate и параметры project-local cache."""
        self._delegate = delegate

        self._provider_identity = provider_identity.strip()

        self._root_path = root_path

        self._namespace = namespace.strip()

        self._ttl_seconds = ttl_seconds

        self._enabled = enabled

        self._single_flights: dict[
            str,
            _SingleFlightEntry,
        ] = {}

        self._single_flights_lock = asyncio.Lock()

        self._prune_lock = asyncio.Lock()

        self._last_prune_monotonic = 0.0

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает canonical cached response либо вызывает delegate один раз."""
        if not self._enabled:
            return await self._delegate.generate_json(
                prompt=prompt,
                schema=schema,
                num_predict=num_predict,
                seed=seed,
                stage=stage,
                image_bytes=image_bytes,
            )

        await self._maybe_prune(
            stage=stage,
        )

        cache_key = self._cache_key(
            prompt=prompt,
            schema=schema,
            num_predict=num_predict,
            seed=seed,
            stage=stage,
            image_bytes=image_bytes,
        )

        cached_payload = await self._load(
            cache_key,
            stage=stage,
        )

        if cached_payload is not None:
            logger.info(
                "[VLM-CACHE:%s] HIT key=%s",
                stage,
                cache_key[:12],
            )

            return self._cached_result(
                payload=cached_payload,
                num_predict=num_predict,
            )

        single_flight = await self._join_single_flight(
            cache_key,
        )

        try:
            async with single_flight.lock:
                cached_payload = await self._load(
                    cache_key,
                    stage=stage,
                )

                if cached_payload is not None:
                    logger.info(
                        ("[VLM-CACHE:%s] HIT_AFTER_WAIT key=%s"),
                        stage,
                        cache_key[:12],
                    )

                    return self._cached_result(
                        payload=cached_payload,
                        num_predict=num_predict,
                    )

                logger.info(
                    "[VLM-CACHE:%s] MISS key=%s",
                    stage,
                    cache_key[:12],
                )

                result = await self._delegate.generate_json(
                    prompt=prompt,
                    schema=schema,
                    num_predict=num_predict,
                    seed=seed,
                    stage=stage,
                    image_bytes=image_bytes,
                )

                await self._save(
                    cache_key,
                    payload=result.payload,
                    stage=stage,
                )

                return result

        finally:
            await self._leave_single_flight(
                cache_key,
                single_flight,
            )

    async def is_ready(
        self,
    ) -> bool:
        """Readiness всегда проверяет реальный shared-vlm."""
        return await self._delegate.is_ready()

    def _cache_key(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None,
    ) -> str:
        """Строит exact-request identity без сохранения raw image в metadata."""
        image_sha256 = (
            hashlib.sha256(
                image_bytes,
            ).hexdigest()
            if image_bytes is not None
            else None
        )

        identity = {
            "cache_key_version": (_CACHE_KEY_VERSION),
            "namespace": self._namespace,
            "provider_identity": (self._provider_identity),
            "stage": stage,
            "seed": seed,
            "num_predict": num_predict,
            "prompt": prompt,
            "schema": schema,
            "image_sha256": image_sha256,
        }

        canonical = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
        ).encode(
            "utf-8",
        )

        return hashlib.sha256(
            canonical,
        ).hexdigest()

    async def _load(
        self,
        cache_key: str,
        *,
        stage: str,
    ) -> dict[str, Any] | None:
        """Best-effort загружает cached payload."""
        try:
            return await asyncio.to_thread(
                self._load_sync,
                cache_key,
            )

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            await asyncio.to_thread(
                self._unlink_quietly,
                self._cache_path(
                    cache_key,
                ),
            )

            logger.warning(
                ("[VLM-CACHE:%s] READ_FAILED key=%s error=%s"),
                stage,
                cache_key[:12],
                type(
                    error,
                ).__name__,
            )

            return None

    def _load_sync(
        self,
        cache_key: str,
    ) -> dict[str, Any] | None:
        """Синхронно читает и валидирует cache record."""
        path = self._cache_path(
            cache_key,
        )

        if not path.is_file():
            return None

        record = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

        if not isinstance(
            record,
            dict,
        ):
            raise ValueError(
                ("VLM cache record должен быть object."),
            )

        if (
            record.get(
                "version",
            )
            != _CACHE_RECORD_VERSION
        ):
            self._unlink_quietly(
                path,
            )

            return None

        created_at = float(
            record.get(
                "created_at",
                0.0,
            )
        )

        if created_at <= 0:
            self._unlink_quietly(
                path,
            )

            return None

        if time.time() - created_at > self._ttl_seconds:
            self._unlink_quietly(
                path,
            )

            return None

        payload = record.get(
            "payload",
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                ("VLM cache payload должен быть object."),
            )

        return payload

    async def _save(
        self,
        cache_key: str,
        *,
        payload: dict[str, Any],
        stage: str,
    ) -> None:
        """Best-effort атомарно сохраняет canonical response."""
        try:
            await asyncio.to_thread(
                self._save_sync,
                cache_key,
                payload,
            )

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            logger.warning(
                ("[VLM-CACHE:%s] WRITE_FAILED key=%s error=%s"),
                stage,
                cache_key[:12],
                type(
                    error,
                ).__name__,
            )

            return

        logger.info(
            "[VLM-CACHE:%s] STORE key=%s",
            stage,
            cache_key[:12],
        )

    def _save_sync(
        self,
        cache_key: str,
        payload: dict[str, Any],
    ) -> None:
        """Синхронно пишет cache record через atomic replace."""
        path = self._cache_path(
            cache_key,
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        record = {
            "version": (_CACHE_RECORD_VERSION),
            "created_at": time.time(),
            "payload": payload,
        }

        serialized = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
        )

        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{cache_key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(
                    serialized,
                )

                temporary.flush()

                os.fsync(
                    temporary.fileno(),
                )

                temporary_path = Path(
                    temporary.name,
                )

            temporary_path.replace(
                path,
            )

        finally:
            if temporary_path is not None and temporary_path.exists():
                self._unlink_quietly(
                    temporary_path,
                )

    async def _join_single_flight(
        self,
        cache_key: str,
    ) -> _SingleFlightEntry:
        """Присоединяет caller к keyed in-process single-flight."""
        async with self._single_flights_lock:
            entry = self._single_flights.get(
                cache_key,
            )

            if entry is None:
                entry = _SingleFlightEntry(
                    lock=asyncio.Lock(),
                )

                self._single_flights[cache_key] = entry

            entry.users += 1

            return entry

    async def _leave_single_flight(
        self,
        cache_key: str,
        entry: _SingleFlightEntry,
    ) -> None:
        """Удаляет keyed lock после ухода последнего caller."""
        async with self._single_flights_lock:
            current = self._single_flights.get(
                cache_key,
            )

            if current is not entry:
                return

            entry.users -= 1

            if entry.users <= 0:
                self._single_flights.pop(
                    cache_key,
                    None,
                )

    async def _maybe_prune(
        self,
        *,
        stage: str,
    ) -> None:
        """Периодически очищает истёкшие project-local cache records."""
        now = time.monotonic()

        if now - self._last_prune_monotonic < _CACHE_PRUNE_INTERVAL_SECONDS:
            return

        async with self._prune_lock:
            now = time.monotonic()

            if now - self._last_prune_monotonic < _CACHE_PRUNE_INTERVAL_SECONDS:
                return

            try:
                removed = await asyncio.to_thread(
                    self._prune_expired_sync,
                )

            except OSError as error:
                logger.warning(
                    ("[VLM-CACHE:%s] PRUNE_FAILED error=%s"),
                    stage,
                    type(
                        error,
                    ).__name__,
                )

                self._last_prune_monotonic = time.monotonic()

                return

            self._last_prune_monotonic = time.monotonic()

            if removed:
                logger.info(
                    ("[VLM-CACHE:%s] PRUNE removed=%s"),
                    stage,
                    removed,
                )

    def _prune_expired_sync(
        self,
    ) -> int:
        """Удаляет records старше configured TTL."""
        if not self._root_path.exists():
            return 0

        cutoff = time.time() - self._ttl_seconds

        removed = 0

        for path in self._root_path.rglob(
            "*.json",
        ):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue

                path.unlink(
                    missing_ok=True,
                )

                removed += 1

            except FileNotFoundError:
                continue

        return removed

    def _cache_path(
        self,
        cache_key: str,
    ) -> Path:
        """Распределяет records по prefix directories."""
        return self._root_path / cache_key[:2] / f"{cache_key}.json"

    @staticmethod
    def _cached_result(
        *,
        payload: dict[str, Any],
        num_predict: int,
    ) -> GenerationResult:
        """Формирует zero-cost metrics для cache hit."""
        content_length = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
            )
        )

        return GenerationResult(
            payload=payload,
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="cache_hit",
                requested_num_predict=(num_predict),
                total_duration_ms=0.0,
                load_duration_ms=0.0,
                prompt_eval_count=0,
                eval_count=0,
                content_length=content_length,
                thinking_length=0,
            ),
        )

    @staticmethod
    def _unlink_quietly(
        path: Path,
    ) -> None:
        """Best-effort удаляет invalid/expired file."""
        try:
            path.unlink(
                missing_ok=True,
            )

        except OSError:
            return
