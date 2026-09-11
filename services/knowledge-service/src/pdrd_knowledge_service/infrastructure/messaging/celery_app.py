# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/celery_app.py

"""Celery configuration Knowledge indexing queues."""

from celery import Celery
from kombu import (
    Exchange,
    Queue,
)

from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.infrastructure.messaging.broker import (
    build_broker_url,
)

settings = get_settings()

broker_settings = settings.broker
technical_settings = settings.technical_assignment_queue

normative_exchange = Exchange(
    broker_settings.exchange_name,
    type="direct",
    durable=True,
)

normative_queue = Queue(
    name=broker_settings.queue_name,
    exchange=normative_exchange,
    routing_key=broker_settings.routing_key,
    durable=True,
    queue_arguments={
        "x-message-ttl": broker_settings.message_ttl_seconds * 1000,
        "x-expires": broker_settings.queue_expires_seconds * 1000,
    },
)

technical_exchange = Exchange(
    technical_settings.exchange_name,
    type="direct",
    durable=True,
)

technical_queue = Queue(
    name=technical_settings.queue_name,
    exchange=technical_exchange,
    routing_key=technical_settings.routing_key,
    durable=True,
    queue_arguments={
        "x-message-ttl": technical_settings.message_ttl_seconds * 1000,
        "x-expires": technical_settings.queue_expires_seconds * 1000,
    },
)

celery_app = Celery(
    "pdrd-knowledge-service",
    broker=build_broker_url(
        broker_settings,
    ),
    include=[
        "pdrd_knowledge_service.infrastructure.messaging.tasks",
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
    task_queues=(
        normative_queue,
        technical_queue,
    ),
    task_default_queue=broker_settings.queue_name,
    task_default_exchange=broker_settings.exchange_name,
    task_default_exchange_type="direct",
    task_default_routing_key=broker_settings.routing_key,
    task_default_delivery_mode="persistent",
    task_create_missing_queues=False,
    task_routes={
        "pdrd.knowledge.normative.index": {
            "queue": broker_settings.queue_name,
            "routing_key": (broker_settings.routing_key),
        },
        "pdrd.knowledge.technical_assignment.index": {
            "queue": technical_settings.queue_name,
            "routing_key": (technical_settings.routing_key),
        },
    },
    task_annotations={
        "pdrd.knowledge.normative.index": {
            "soft_time_limit": broker_settings.task_soft_time_limit_seconds,
            "time_limit": broker_settings.task_hard_time_limit_seconds,
        },
        "pdrd.knowledge.technical_assignment.index": {
            "soft_time_limit": technical_settings.task_soft_time_limit_seconds,
            "time_limit": technical_settings.task_hard_time_limit_seconds,
        },
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=(broker_settings.connect_timeout_seconds),
    broker_heartbeat=30,
    broker_transport_options={
        "confirm_publish": True,
    },
)
