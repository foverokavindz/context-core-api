import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from app.core.exceptions import EmbeddingConfigurationError, EmbeddingError

logger = logging.getLogger(__name__)

load_dotenv()

EMBEDDING_BATCH_SIZE = 30

EMBEDDING_DIMENSIONS = 1536

MAX_EMBEDDING_INPUT_CHARS = 24_000

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


class EmbeddableChunk(Protocol):
    """What this module needs from a chunk: text in, vector out.
    """

    content: str
    embedding: list[float] | None
    embedding_model: str | None


class EmbeddableResult(Protocol):
    """What `embed_into` needs from an ingestion result.
    """

    chunks: Sequence[EmbeddableChunk]
    embedded_chunks: int
    embedding_batches: int
    embedding_model: str | None
    embedding_dimensions: int | None
    embedding_truncated_inputs: int


@dataclass
class EmbeddingRun:
    """What one embedding pass did, for the response and the log line."""

    embedded: int
    batches: int
    model: str
    dimensions: int
    truncated_inputs: int = 0


class ChunkEmbedder:
    """Embeds CodeChunk content against an OpenAI-compatible endpoint.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        batch_size: int = EMBEDDING_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        self._base_url = base_url
        self._api_key = api_key
        self._deployment = deployment
        self.batch_size = batch_size
        self._client: OpenAI | None = None

    @property
    def model(self) -> str:
        """The deployment name, which is both what we call and what we record."""
        return self._deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""

    #  public

    def embed_chunks(self, chunks: Sequence[EmbeddableChunk]) -> EmbeddingRun:
        """Embed every chunk and attach the vectors to those same objects.
        """
        if not chunks:
            return EmbeddingRun(embedded=0, batches=0, model=self.model, dimensions=0)

        texts: list[str] = []
        truncated = 0
        for chunk in chunks:
            text, was_truncated = _clip(chunk.content)
            texts.append(text)
            if was_truncated:
                truncated += 1

        if truncated:
            logger.info(
                "%d chunk(s) exceeded %d characters and were truncated for "
                "embedding; their stored content is unchanged",
                truncated,
                MAX_EMBEDDING_INPUT_CHARS,
            )

        embeddings = self.embed_texts(texts)

        if len(embeddings) != len(chunks):
            logger.error(
                "Collected %d vectors for %d chunks; no chunk has been modified",
                len(embeddings),
                len(chunks),
            )
            raise EmbeddingError(
                f"The embedding service returned {len(embeddings)} vectors for "
                f"{len(chunks)} chunks."
            )

        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk.embedding = embedding
            chunk.embedding_model = self.model

        return EmbeddingRun(
            embedded=len(chunks),
            batches=_batch_count(len(chunks), self.batch_size),
            model=self.model,
            dimensions=len(embeddings[0]),
            truncated_inputs=truncated,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, returning one vector per text, in order.
        """
        if not texts:
            return []

        self._ensure_client()

        collected: list[list[float]] = []
        total_batches = _batch_count(len(texts), self.batch_size)

        logger.info(
            "Embedding %d chunks in %d request(s) of at most %d",
            len(texts),
            total_batches,
            self.batch_size,
        )

        for number, start in enumerate(range(0, len(texts), self.batch_size), start=1):
            batch = texts[start : start + self.batch_size]

            logger.info(
                "[%d/%d] embedding %d chunks (%d characters)",
                number,
                total_batches,
                len(batch),
                sum(len(text) for text in batch),
            )

            items = self._request(batch)

            if len(items) != len(batch):
 
                logger.error(
                    "Batch %d/%d sent %d chunks and got %d vectors back; "
                    "abandoning the run rather than guessing the alignment",
                    number,
                    total_batches,
                    len(batch),
                    len(items),
                )
                raise EmbeddingError(
                    f"The embedding service returned {len(items)} vectors for a "
                    f"batch of {len(batch)} chunks."
                )

            # By index, not by arrival. The SDK preserves order today; this
            # makes the guarantee ours rather than borrowed.
            for item in sorted(items, key=lambda entry: entry.index):
                vector = list(item.embedding)
                if len(vector) != EMBEDDING_DIMENSIONS:
                    logger.error(
                        "Batch %d/%d returned a %d-dimension vector; this "
                        "application stores %d. Check which deployment %s points at",
                        number,
                        total_batches,
                        len(vector),
                        EMBEDDING_DIMENSIONS,
                        self.model,
                    )
                    raise EmbeddingError(
                        f"The embedding service returned a {len(vector)}-dimension "
                        f"vector; this application stores {EMBEDDING_DIMENSIONS}."
                    )
                collected.append(vector)

        return collected

    #  private

    def _request(self, batch: list[str]) -> list:
        """Send one batch, retrying only what is worth retrying.
        """
        client = self._ensure_client()
        model = self.model

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.embeddings.create(model=model, input=batch)
            except (RateLimitError, APIConnectionError) as exc:
                if attempt == MAX_ATTEMPTS:
                    logger.error(
                        "Embedding request failed after %d attempts: %s",
                        MAX_ATTEMPTS,
                        type(exc).__name__,
                    )
                    raise EmbeddingError() from exc
                delay = RETRY_BACKOFF_SECONDS ** attempt
                logger.warning(
                    "Embedding request failed (%s); retrying in %.0fs (%d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt,
                    MAX_ATTEMPTS,
                )
                time.sleep(delay)
            except APIStatusError as exc:

                logger.error(
                    "Embedding request rejected with HTTP %d", exc.status_code
                )
                raise EmbeddingError() from exc
            else:
                return list(response.data)

        # Unreachable: the loop either returns or raises.
        raise EmbeddingError()

    def _ensure_client(self) -> OpenAI:
        """Build the client on first use, and complain clearly if it cannot be.
        """
        if self._client is not None:
            return self._client

        base_url = self._base_url or os.getenv("AZURE_OPENAI_BASE_URL")
        api_key = self._api_key or os.getenv("AZURE_OPENAI_API_KEY")
        deployment = self._deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT")

        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_BASE_URL", base_url),
                ("AZURE_OPENAI_API_KEY", api_key),
                ("AZURE_OPENAI_DEPLOYMENT", deployment),
            )
            if not value
        ]
        if missing:
            # Names only. The values are the secret.
            logger.error("Embedding is not configured: missing %s", ", ".join(missing))
            raise EmbeddingConfigurationError()

        self._deployment = deployment

        logger.info("Embedding with deployment %s", deployment)
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        return self._client


# the pipeline stage


def embed_into(
    result: EmbeddableResult,
    embedder: ChunkEmbedder | None,
    *,
    embed: bool = True,
) -> None:
    """Embed a run's chunks and record what that took on the result.
    """
    if not embed:
        logger.info("Embedding skipped at the caller's request")
        return

    if embedder is None:
        if result.chunks:
            logger.info("No embedder is configured; chunks have no vectors")
        return

    if not result.chunks:
        return

    started = time.monotonic()
    run = embedder.embed_chunks(result.chunks)

    result.embedded_chunks = run.embedded
    result.embedding_batches = run.batches
    result.embedding_model = run.model
    result.embedding_dimensions = run.dimensions
    result.embedding_truncated_inputs = run.truncated_inputs

    logger.info(
        "Embedded %d chunks into %d-dimension vectors with %s "
        "(%d embedding API calls) in %.1fs",
        run.embedded,
        run.dimensions,
        run.model,
        run.batches,
        time.monotonic() - started,
    )


#  helpers


def _clip(content: str) -> tuple[str, bool]:
    """Return the text to embed, and whether it had to be shortened."""
    if len(content) <= MAX_EMBEDDING_INPUT_CHARS:
        return content, False
    return content[:MAX_EMBEDDING_INPUT_CHARS], True


def _batch_count(items: int, size: int) -> int:
    """How many requests `items` takes at `size` per request."""
    return (items + size - 1) // size
