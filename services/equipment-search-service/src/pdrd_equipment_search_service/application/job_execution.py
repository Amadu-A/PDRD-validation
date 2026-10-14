# services/equipment-search-service/src/pdrd_equipment_search_service/application/job_execution.py

"""Справедливые проходы по моделям с долговечным checkpoint после каждой попытки."""

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import UUID

from pdrd_equipment_search_service.application.resolve import SearchCancelled
from pdrd_equipment_search_service.application.search_budget import (
    EquipmentSearchBudget,
)
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.core.observability import log_execution_time
from pdrd_equipment_search_service.domain.diagnostics import ReasonCode
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
)

if TYPE_CHECKING:
    from pdrd_equipment_search_service.application.jobs import SearchJobService


@log_execution_time(operation="equipment.batch")
async def run_search_job(service: "SearchJobService", job_id: UUID) -> None:
    """Обходит все модели до дополнительных попыток, освобождая слот между порциями."""
    if not await service.repository.claim(job_id):
        return
    state = await service.repository.get(job_id)
    if state is None:
        return
    policy = state.get("policy") or service.policy(len(state["identities"]))
    checkpoint = dict(state.get("checkpoint") or {})
    generation = int(state.get("resume_count") or 0)
    new_generation = checkpoint.get("generation", 0) != generation
    if new_generation:
        checkpoint = {"round": 0, "cursor": 0}
    checkpoint["generation"] = generation
    results = list(state.get("results") or [])
    if len(results) != len(state["identities"]):
        results = [
            {
                "identity": identity,
                "status": "not_checked",
                "reason_code": ReasonCode.NOT_CHECKED,
                "metrics": {},
                "attempts": [],
                "retryable": True,
            }
            for identity in state["identities"]
        ]
    if new_generation:
        for row in results:
            if row.get("status") not in {"found", "local"}:
                row["retryable"] = True
                row["tried_url_hashes"] = []
    queries_used = int(checkpoint.get("queries_used") or 0)
    downloads_used = int(checkpoint.get("downloads_used") or 0)
    per_model_downloads = dict(checkpoint.get("per_model_downloads") or {})
    vision = EquipmentVisionBudget(
        max(0, policy["vision_limit"] - int(checkpoint.get("vision_used") or 0))
    )
    vision_used = int(checkpoint.get("vision_used") or 0)
    if checkpoint.get("inflight"):
        index = int(checkpoint["inflight"]["index"])
        results[index]["attempts"] = [
            *results[index].get("attempts", []),
            {"stage": "operation", "code": "operation_interrupted"},
        ]
        results[index]["metrics"]["interrupted_attempts"] = (
            int(results[index]["metrics"].get("interrupted_attempts") or 0) + 1
        )
    await service.repository.append_event(
        job_id, "started", "Поиск документации начат."
    )
    status, warning = "completed", ""
    try:
        for round_index in range(
            int(checkpoint.get("round") or 0), policy["queries_per_model"]
        ):
            cursor = (
                int(checkpoint.get("cursor") or 0)
                if round_index == checkpoint.get("round", 0)
                else 0
            )
            for begin in range(cursor, len(results), service.batch_size):
                for index in range(
                    begin, min(begin + service.batch_size, len(results))
                ):
                    async with service._semaphore:
                        service._active.add(job_id)
                        try:
                            if await service.repository.is_cancelled(job_id):
                                raise SearchCancelled
                            previous = results[index]
                            if previous.get("status") in {"found", "local"}:
                                continue
                            if round_index > 0 and not previous.get("retryable", False):
                                continue
                            identity = EquipmentIdentity(**state["identities"][index])
                            await service.repository.append_event(
                                job_id,
                                "model_started",
                                f"Поиск документации: {identity.model}"[:200],
                            )
                            budget = EquipmentSearchBudget(
                                min(1, max(0, policy["query_limit"] - queries_used)),
                                min(
                                    1,
                                    max(0, policy["download_limit"] - downloads_used),
                                    max(
                                        0,
                                        policy["downloads_per_model"]
                                        - per_model_downloads.get(str(index), 0),
                                    ),
                                ),
                            )
                            queries_before, downloads_before = (
                                budget.queries_remaining,
                                budget.downloads_remaining,
                            )
                            remaining_before, metric_offset = (
                                vision.remaining,
                                len(vision.metrics),
                            )
                            # Резерв сохраняется ДО внешней операции. При аварии квота
                            # остаётся израсходованной: рестарты не обходят лимиты.
                            reservation = {
                                "round": round_index,
                                "cursor": index,
                                "generation": generation,
                                "queries_used": queries_used + queries_before,
                                "downloads_used": downloads_used + downloads_before,
                                "vision_used": vision_used + vision.remaining,
                                "per_model_downloads": {
                                    **per_model_downloads,
                                    str(index): per_model_downloads.get(str(index), 0)
                                    + downloads_before,
                                },
                                "inflight": {"index": index},
                            }
                            await service.repository.checkpoint(
                                job_id, results, reservation
                            )
                            started = perf_counter()
                            try:
                                async with asyncio.timeout(service.max_seconds):
                                    result = await service.resolver.execute(
                                        identity,
                                        allow_unverified=state["allow_unverified"],
                                        should_stop=lambda: (
                                            service.repository.is_cancelled(job_id)
                                        ),
                                        vision_budget=vision,
                                        search_budget=budget,
                                        round_index=round_index,
                                        previous=previous,
                                    )
                            except TimeoutError:
                                result = ResolveResult(
                                    identity,
                                    "incomplete",
                                    reason_code=ReasonCode.TIMEOUT,
                                    retryable=True,
                                )
                            except (SearchCancelled, asyncio.CancelledError):
                                raise
                            except Exception:
                                result = ResolveResult(
                                    identity,
                                    "incomplete",
                                    reason_code=ReasonCode.INTERNAL_ERROR,
                                    retryable=True,
                                )
                            model_metrics = vision.metrics[metric_offset:]
                            cached = sum(
                                metric.get("done_reason") == "cache_hit"
                                for metric in model_metrics
                            )
                            calls = max(0, remaining_before - vision.remaining)
                            vision_used += calls
                            queries = queries_before - budget.queries_remaining
                            downloads = downloads_before - budget.downloads_remaining
                            queries_used += queries
                            downloads_used += downloads
                            per_model_downloads[str(index)] = (
                                per_model_downloads.get(str(index), 0) + downloads
                            )
                            metrics = dict(previous.get("metrics") or {})
                            metrics["downloads_in_run"] = per_model_downloads[
                                str(index)
                            ]
                            metrics["download_limit_per_run"] = policy[
                                "downloads_per_model"
                            ]
                            metrics["run_number"] = generation + 1
                            delta = {
                                "duration_ms": round(
                                    (perf_counter() - started) * 1000, 3
                                ),
                                "queries": queries,
                                "downloads": downloads,
                                "vision_calls": calls,
                                "vision_cache_hits": cached,
                                "resolution_attempts": 1,
                                "verified_documents": sum(
                                    attempt.get("code") == "verified"
                                    for attempt in result.attempts
                                ),
                                "prompt_tokens": sum(
                                    int(metric.get("prompt_eval_count") or 0)
                                    for metric in model_metrics
                                ),
                                "output_tokens": sum(
                                    int(metric.get("eval_count") or 0)
                                    for metric in model_metrics
                                ),
                            }
                            for key, value in delta.items():
                                metrics[key] = metrics.get(key, 0) + value
                            metrics["vision_metrics"] = [
                                *metrics.get("vision_metrics", []),
                                *model_metrics,
                            ][-48:]
                            results[index] = {
                                **asdict(result),
                                "metrics": metrics,
                                "attempts": [
                                    *previous.get("attempts", []),
                                    *result.attempts,
                                ][-96:],
                            }
                            checkpoint = {
                                "round": round_index,
                                "cursor": index + 1,
                                "model_index": index,
                                "generation": generation,
                                "queries_used": queries_used,
                                "downloads_used": downloads_used,
                                "vision_used": vision_used,
                                "per_model_downloads": per_model_downloads,
                            }
                            await service.repository.checkpoint(
                                job_id, results, checkpoint
                            )
                            await service.repository.append_event(
                                job_id,
                                "model_finished",
                                f"Модель {identity.model}: {result.status}"[:200],
                            )
                        finally:
                            service._active.discard(job_id)
                await asyncio.sleep(0)
                end = min(begin + service.batch_size, len(results))
                more_this_round = any(
                    row.get("status") not in {"found", "local"}
                    and (round_index == 0 or row.get("retryable", False))
                    for row in results[end:]
                )
                more_next_round = round_index + 1 < policy["queries_per_model"] and any(
                    row.get("status") not in {"found", "local"}
                    and row.get("retryable", False)
                    for row in results
                )
                if policy.get("background") and (more_this_round or more_next_round):
                    checkpoint.update(
                        round=round_index if more_this_round else round_index + 1,
                        cursor=end if more_this_round else 0,
                    )
                    await service.repository.checkpoint(job_id, results, checkpoint)
                    if not await service.repository.release(job_id):
                        raise SearchCancelled
                    await service.repository.append_event(
                        job_id,
                        "batch_finished",
                        "Порция сохранена; проверка продолжается.",
                    )
                    return
    except SearchCancelled:
        status, warning = "cancelled", "Поиск отменён пользователем."
    except asyncio.CancelledError:
        if await service.repository.is_cancelled(job_id):
            status, warning = "cancelled", "Поиск отменён пользователем."
        else:
            # running и последний checkpoint остаются для восстановления следующего процесса.
            raise
    except Exception:
        status, warning = "incomplete", "Ошибка обработки порции оборудования."
    if status == "completed" and any(
        row.get("status") in {"incomplete", "not_checked", "needs_review", "not_found"}
        for row in results
    ):
        status = "incomplete"
    await service.repository.finish(job_id, status, results, warning)
    await service.repository.append_event(job_id, status, warning or "Поиск завершён.")
