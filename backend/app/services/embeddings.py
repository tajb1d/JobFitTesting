"""Text embeddings. The only module that talks to Voyage (CLAUDE.md)."""

import time
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


class VoyageProvider:
    """Voyage REST API over httpx. input_type is per request, so callers make one call per
    type (e.g. resume as "query", its bullets as "document")."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = VOYAGE_MODEL,
        dimension: int = EMBEDDING_DIM,
        batch_size: int = 128,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=30)

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[i : i + self.batch_size], input_type))
        return vectors

    def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimension,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.post(VOYAGE_URL, json=payload, headers=headers)
            except httpx.HTTPError as e:
                error: str = f"request failed: {e}"
            else:
                if r.status_code == 200:
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    vectors = [d["embedding"] for d in data]
                    if len(vectors) != len(texts) or any(len(v) != self.dimension for v in vectors):
                        raise EmbeddingError("Voyage returned unexpected vector count or size")
                    return vectors
                error = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code != 429 and r.status_code < 500:
                    raise EmbeddingError(error)  # our fault (bad key, bad input): don't retry
            if attempt < self.max_retries:
                time.sleep(2**attempt)
        raise EmbeddingError(error)


@lru_cache
def get_embedder() -> EmbeddingProvider:
    """FastAPI dependency. Tests override it with a fake."""
    return VoyageProvider(get_settings().voyage_api_key)
