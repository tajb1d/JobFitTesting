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


# ---------------------------------------------------------------- ingestion pacing


class FakeClock:
    """time.monotonic/time.sleep stand-in: sleeping advances the clock instantly."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(embeddings.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(embeddings.time, "sleep", clock.sleep)
    return clock


def _ok_with_usage(tokens_per_text: int):
    def handler(request):
        response = _ok(request)
        body = json.loads(response.content)
        body["usage"] = {"total_tokens": tokens_per_text * len(body["data"])}
        return httpx.Response(200, json=body)
    return handler


def test_requests_per_minute_is_respected(clock):
    sent_at = []

    def handler(request):
        sent_at.append(clock.now)
        return _ok(request)

    provider = _provider(handler, batch_size=1, requests_per_minute=3)
    provider.embed(["a", "b", "c", "d", "e"], "document")
    # 3 go out immediately; the 4th waits until the first leaves the 60 s window.
    assert sent_at[:3] == [1000.0] * 3
    assert sent_at[3] >= 1060.0 and sent_at[4] >= 1060.0
    assert provider.request_count == 5


def test_tokens_per_minute_splits_batches_and_paces_by_actual_usage(clock):
    sizes, sent_at = [], []
    ok = _ok_with_usage(tokens_per_text=400)

    def handler(request):
        sizes.append(len(json.loads(request.content)["input"]))
        sent_at.append(clock.now)
        return ok(request)

    text = "x" * 1200  # estimated at 401 tokens
    provider = _provider(handler, tokens_per_minute=1000)
    provider.embed([text] * 4, "document")
    assert sizes == [2, 2]                 # 900-token request budget (90% of 1000)
    assert sent_at[1] - sent_at[0] >= 60   # 800 used + 800 more > 1000 per minute
    assert provider.token_count == 1600


def test_rate_limit_backoff_applies_to_429(clock):
    calls = []

    def handler(request):
        calls.append(clock.now)
        return httpx.Response(429, text="slow down") if len(calls) == 1 else _ok(request)

    _provider(handler, rate_limit_backoff=30).embed(["a"], "query")
    assert clock.sleeps == [30]


def test_estimate_tokens_is_conservative():
    assert embeddings.estimate_tokens("x" * 400) >= 100  # English averages ~4 chars/token
