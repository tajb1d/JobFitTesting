"""Text embeddings. The only module that talks to Voyage (CLAUDE.md)."""

import time
from collections import deque
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal, Protocol

import httpx

from app.config import EMBEDDING_DIM, get_settings

InputType = Literal["query", "document"]

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-4-lite"


class EmbeddingError(Exception):
    """The embedding provider failed after retries."""


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        """One vector of EMBEDDING_DIM floats per input text, in input order."""
        ...


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (English runs ~4 chars/token) used only for pacing."""
    return len(text) // 3 + 1


class VoyageProvider:
    """Voyage REST API over httpx. input_type is per request, so callers make one call per
    type (e.g. resume as "query", its bullets as "document").

    requests_per_minute / tokens_per_minute pace requests client-side over a sliding 60 s
    window (used by batch ingestion; request-time calls leave them unset). tokens_per_minute
    also caps how many tokens go into one request, so a batch never exceeds the window."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = VOYAGE_MODEL,
        dimension: int = EMBEDDING_DIM,
        batch_size: int = 128,
        max_retries: int = 2,
        requests_per_minute: int | None = None,
        tokens_per_minute: int | None = None,
        rate_limit_backoff: float = 0.0,
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.rate_limit_backoff = rate_limit_backoff
        self.client = client or httpx.Client(timeout=30)
        self._window: deque[tuple[float, int]] = deque()  # (sent at, tokens) of recent requests
        # Usage totals, reported by the ingestion summary.
        self.request_count = 0
        self.token_count = 0

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(self._embed_batch(batch, input_type))
        return vectors

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        # Leave headroom under the per-minute budget for estimate error.
        max_tokens = int(self.tokens_per_minute * 0.9) if self.tokens_per_minute else None
        batch: list[str] = []
        batch_tokens = 0
        for text in texts:
            tokens = estimate_tokens(text)
            if batch and (len(batch) >= self.batch_size
                          or (max_tokens and batch_tokens + tokens > max_tokens)):
                yield batch
                batch, batch_tokens = [], 0
            batch.append(text)
            batch_tokens += tokens
        if batch:
            yield batch

    def _wait_for_capacity(self, tokens: int) -> None:
        if not (self.requests_per_minute or self.tokens_per_minute):
            return
        while True:
            now = time.monotonic()
            while self._window and now - self._window[0][0] >= 60:
                self._window.popleft()
            used = sum(t for _, t in self._window)
            requests_ok = not self.requests_per_minute or len(self._window) < self.requests_per_minute
            # An oversized single request is allowed once the window is empty.
            tokens_ok = (not self.tokens_per_minute or not self._window
                         or used + tokens <= self.tokens_per_minute)
            if requests_ok and tokens_ok:
                return
            time.sleep(max(0.1, 60 - (now - self._window[0][0])))

    def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimension,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        estimate = sum(estimate_tokens(t) for t in texts)
        for attempt in range(self.max_retries + 1):
            self._wait_for_capacity(estimate)
            wait = float(2**attempt)
            try:
                r = self.client.post(VOYAGE_URL, json=payload, headers=headers)
            except httpx.HTTPError as e:
                error: str = f"request failed: {e}"
            else:
                self.request_count += 1
                if r.status_code == 200:
                    body = r.json()
                    used = int(body.get("usage", {}).get("total_tokens", estimate))
                    self.token_count += used
                    self._window.append((time.monotonic(), used))
                    data = sorted(body["data"], key=lambda d: d["index"])
                    vectors = [d["embedding"] for d in data]
                    if len(vectors) != len(texts) or any(len(v) != self.dimension for v in vectors):
                        raise EmbeddingError("Voyage returned unexpected vector count or size")
                    return vectors
                error = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code != 429 and r.status_code < 500:
                    raise EmbeddingError(error)  # our fault (bad key, bad input): don't retry
                if r.status_code == 429:
                    self._window.append((time.monotonic(), estimate))
                    wait = max(wait, self.rate_limit_backoff)
            if attempt < self.max_retries:
                time.sleep(wait)
        raise EmbeddingError(error)


@lru_cache
def get_embedder() -> EmbeddingProvider:
    """FastAPI dependency. Tests override it with a fake."""
    return VoyageProvider(get_settings().voyage_api_key)
