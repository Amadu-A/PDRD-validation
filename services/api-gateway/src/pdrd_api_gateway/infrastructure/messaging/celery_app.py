# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/celery_app.py

"""Конфигурация Celery worker проекта PDRD."""

from celery import Celery
from kombu import Exchange, Queue

from pdrd_api_gateway.core.settings import get_settings
from pdrd_api_gateway.infrastructure.messaging.broker import (
    build_broker_url,
)

settings = get_settings()
broker_settings = settings.broker

analysis_exchange = Exchange(
    broker_settings.exchange_name,
    type="direct",
    durable=True,
)

analysis_queue = Queue(
    name=broker_settings.queue_name,
    exchange=analysis_exchange,
    routing_key=broker_settings.routing_key,
    durable=True,
)

celery_app = Celery(
    "pdrd-api-gateway",
    broker=build_broker_url(
        broker_settings,
    ),
    backend="rpc://",
    include=[
        "pdrd_api_gateway.infrastructure.messaging.tasks",
    ],
)

celery_app.conf.update(
    accept_content=[
        "json",
    ],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_queues=(analysis_queue,),
    task_default_queue=broker_settings.queue_name,
    task_default_exchange=broker_settings.exchange_name,
    task_default_exchange_type="direct",
    task_default_routing_key=broker_settings.routing_key,
    task_default_delivery_mode="persistent",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    task_ignore_result=True,
    result_expires=broker_settings.result_expires_seconds,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=(broker_settings.connect_timeout_seconds),
    broker_heartbeat=30,
)
