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


def test_text_embedding_release_is_outside_per_input_loop() -> None:
    """Knowledge adapter releases checkpoint после всего embed batch."""
    text_source = TEXT_EMBEDDING_PATH.read_text(
        encoding="utf-8",
    )

    multimodal_source = MULTIMODAL_EMBEDDING_PATH.read_text(
        encoding="utf-8",
    )

    assert text_source.count("await self._delegate.release()") == 1

    assert "for item in inputs:" in multimodal_source

    assert "await self.release()" not in multimodal_source

    assert "await self._delegate.release()" not in multimodal_source
