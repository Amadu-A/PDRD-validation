# services/knowledge-service/tests/unit/test_experience_feed_http.py

"""Настоящий HTTP-адаптер E отклоняет повреждённый контракт, чужую редакцию и неверный PNG."""

import copy

import httpx
import pytest
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.infrastructure.experience_feed import (
    HttpExperienceFeed,
    parse_example,
)

from tests.experience_index_support import PNG, signed, trusted_example


@pytest.mark.parametrize(
    "problem",
    [
        "pending",
        "adjudication",
        "polarity",
        "duplicate_target",
        "wrong_target_text",
        "hash",
        "dimensions",
        "revision_bool",
        "source",
        "bad_gold",
        "no_reason",
        "bad_structure",
    ],
)
def test_adapter_rejects_semantically_invalid_projection_even_with_valid_fingerprint(
    problem,
):
    """Пересчитанный SHA не назначает неверной разметке доверие."""
    data = copy.deepcopy(trusted_example(tag="edited", target="both").data)
    if problem == "pending":
        data["decision"] = "pending"
    elif problem == "adjudication":
        data["learning_use"] = "needs_adjudication"
    elif problem == "polarity":
        data["decision"] = "accepted"
    elif problem == "duplicate_target":
        data["texts"][1] = data["texts"][0]
    elif problem == "wrong_target_text":
        data["texts"][0]["text"] = "Чужая формулировка"
    elif problem == "hash":
        data["crops"][0]["sha256"] = "g" * 64
    elif problem == "dimensions":
        data["crops"][0]["width"] = -1
    elif problem == "revision_bool":
        data["example_revision"] = True
    elif problem == "source":
        data["source_sha256"] = "?" * 64
    elif problem == "bad_gold":
        data["tag"] = "gold"
    elif problem == "no_reason":
        data["rejection_reason"] = " "
    else:
        data["texts"] = "original"
    with pytest.raises(ExperienceFeedError):
        parse_example(signed(data))


async def test_adapter_passes_server_key_and_cursor_and_checks_crop_hash():
    """Никакой ключ не приходит из browser; изображение сверяется до embedding."""
    item = trusted_example()
    requests = []

    def handle(request):
        """Проверяет фактические URL и Authorization входящего HTTP-вызова."""
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-server-key"
        if "/crops/" in request.url.path:
            assert request.url.params["fingerprint"] == item.reference.fingerprint
            return httpx.Response(200, content=PNG)
        assert request.url.params["after"] == str(item.reference.example_id)
        return httpx.Response(
            200,
            json={"items": [item.data], "next_after": str(item.reference.example_id)},
        )

    feed = HttpExperienceFeed(
        "http://experience/",
        "private-server-key",
        transport=httpx.MockTransport(handle),
    )
    examples, cursor = await feed.page(after=item.reference.example_id, limit=1)
    assert examples == (item,) and cursor == item.reference.example_id
    assert await feed.crop(example=item, index=0) == PNG
    assert len(requests) == 2


@pytest.mark.parametrize("content", [b"not-png", b"\x89PNG\r\n\x1a\nchanged"])
async def test_wrong_crop_content_is_never_sent_to_embedding(content):
    """PNG-сигнатура сама по себе не заменяет проверку digest."""
    feed = HttpExperienceFeed(
        "http://experience",
        "key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content)
        ),
    )
    with pytest.raises(ExperienceFeedError):
        await feed.crop(example=trusted_example(), index=0)


@pytest.mark.parametrize("problem", ["duplicate", "unsolicited", "malformed", "outage"])
async def test_verify_does_not_accept_extra_or_unrequested_source_versions(problem):
    """Неизвестный серверный ответ не позволяет выдать cached vector payload."""
    item = trusted_example()
    if problem == "duplicate":
        body = {"items": [item.data, item.data]}
    elif problem == "unsolicited":
        body = {"items": [trusted_example(number=2).data]}
    else:
        body = {"items": [None]}
    feed = HttpExperienceFeed(
        "http://experience",
        "key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503 if problem == "outage" else 200, json=body
            )
        ),
    )
    with pytest.raises(ExperienceFeedError):
        await feed.verify((item.reference,))
