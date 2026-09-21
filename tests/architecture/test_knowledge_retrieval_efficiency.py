# tests/architecture/test_knowledge_retrieval_efficiency.py

"""Architecture guards эффективности Knowledge Service retrieval."""

import ast
from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

ROUTER_PATH = (
    ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
    / "transport"
    / "http"
    / "routers"
    / "search.py"
)

NORMATIVE_USE_CASE_PATH = (
    ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
    / "application"
    / "use_cases"
    / "normative.py"
)

TEXT_EMBEDDING_PATH = (
    ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
    / "infrastructure"
    / "embedding"
    / "text_http.py"
)

MULTIMODAL_EMBEDDING_PATH = (
    ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
    / "infrastructure"
    / "embedding"
    / "multimodal_http.py"
)


def _async_function(
    path: Path,
    *,
    name: str,
) -> ast.AsyncFunctionDef:
    """Находит async function в Python source."""
    tree = ast.parse(
        path.read_text(
            encoding="utf-8",
        )
    )

    for node in ast.walk(
        tree,
    ):
        if (
            isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name == name
        ):
            return node

    raise AssertionError(
        f"Не найдена async function {name} в {path}.",
    )


def _called_attributes(
    function: ast.AsyncFunctionDef,
) -> list[str]:
    """Возвращает имена вызываемых attribute methods."""
    return [
        node.func.attr
        for node in ast.walk(
            function,
        )
        if (
            isinstance(
                node,
                ast.Call,
            )
            and isinstance(
                node.func,
                ast.Attribute,
            )
        )
    ]


def test_grouped_http_route_uses_one_application_batch_call() -> None:
    """Route не должен вызывать SearchNormative.execute по одному query."""
    function = _async_function(
        ROUTER_PATH,
        name="search_normative_grouped",
    )

    called = _called_attributes(
        function,
    )

    assert (
        called.count(
            "execute_grouped",
        )
        == 1
    )

    assert "execute" not in called


def test_grouped_use_case_contains_one_embedding_call_site() -> None:
    """Grouped use case имеет один embedding batch call site."""
    function = _async_function(
        NORMATIVE_USE_CASE_PATH,
        name="execute_grouped",
    )

    called = _called_attributes(
        function,
    )

    assert (
        called.count(
            "embed",
        )
        == 1
    )

    assert "execute" not in called


def test_embedding_adapters_use_one_resident_batch_without_release_lifecycle() -> None:
    """Shared vLLM получает batch одним HTTP call без model unload."""
    text_function = _async_function(
        TEXT_EMBEDDING_PATH,
        name="embed",
    )

    text_calls = _called_attributes(
        text_function,
    )

    assert (
        text_calls.count(
            "embed",
        )
        == 1
    )

    assert "release" not in text_calls

    multimodal_function = _async_function(
        MULTIMODAL_EMBEDDING_PATH,
        name="embed",
    )

    multimodal_calls = _called_attributes(
        multimodal_function,
    )

    assert (
        multimodal_calls.count(
            "post",
        )
        == 1
    )

    assert "release" not in multimodal_calls

    text_source = TEXT_EMBEDDING_PATH.read_text(
        encoding="utf-8",
    )

    multimodal_source = MULTIMODAL_EMBEDDING_PATH.read_text(
        encoding="utf-8",
    )

    assert "await self._delegate.release()" not in text_source

    assert "/internal/v1/embeddings" not in multimodal_source

    assert "/internal/v1/release" not in multimodal_source

    assert "/embeddings" in multimodal_source

    assert "shared-embedding" in multimodal_source
