import json

import httpx
import pytest

from app.config import EMBEDDING_DIM
from app.services import embeddings
from app.services.embeddings import EmbeddingError, VoyageProvider


def _provider(handler, **kw) -> VoyageProvider:
    return VoyageProvider("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def _ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    # Return out of order to prove the provider sorts by index.
    data = [
        {"index": i, "embedding": [float(i)] * body["output_dimension"]}
        for i in range(len(body["input"]))
    ][::-1]
    return httpx.Response(200, json={"data": data, "model": body["model"]})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)


def test_request_shape_and_order():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        return _ok(request)

    vectors = _provider(handler).embed(["a", "b", "c"], "document")
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    assert seen == [
        {"input": ["a", "b", "c"], "model": "voyage-4-lite", "input_type": "document",
         "output_dimension": EMBEDDING_DIM}
    ]


def test_batches_large_inputs():
    sizes = []

    def handler(request):
        sizes.append(len(json.loads(request.content)["input"]))
        return _ok(request)

    vectors = _provider(handler, batch_size=2).embed(["a", "b", "c", "d", "e"], "document")
    assert sizes == [2, 2, 1]
    assert len(vectors) == 5


def test_empty_input_makes_no_request():
    def handler(request):
        raise AssertionError("no request expected")

    assert _provider(handler).embed([], "query") == []


def test_retries_rate_limits_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, text="slow down") if len(calls) < 3 else _ok(request)

    assert len(_provider(handler).embed(["a"], "query")) == 1
    assert len(calls) == 3


def test_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="down")

    with pytest.raises(EmbeddingError, match="503"):
        _provider(handler).embed(["a"], "query")
    assert len(calls) == 3  # first try + 2 retries


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(EmbeddingError, match="401"):
        _provider(handler).embed(["a"], "query")
    assert len(calls) == 1


def test_network_errors_are_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return _ok(request)

    assert len(_provider(handler).embed(["a"], "query")) == 1


def test_wrong_dimension_rejected():
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 3}]})

    with pytest.raises(EmbeddingError, match="unexpected"):
        _provider(handler).embed(["a"], "query")


def test_missing_api_key():
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        VoyageProvider("").embed(["a"], "query")
