"""Turns chunk text into vectors, in batches, without ever losing the order.

This is the only module in the project that imports `openai`, the same way
`github_connector.py` is the only one that imports PyGithub. Everything else
sees `embed_chunks(chunks)` and a list of CodeChunks that now carry vectors.

The configuration is the one proved out in `test.py`: an Azure OpenAI deployment
addressed through its `/openai/v1` surface, which is OpenAI-compatible - so the
plain `OpenAI` client is correct here and `AzureOpenAI` is not. `model` is the
*deployment* name rather than a model name, which is why it is also what gets
recorded as `embedding_model`.

    AZURE_OPENAI_BASE_URL     https://<resource>.openai.azure.com/openai/v1
    AZURE_OPENAI_API_KEY      the key; never logged, never returned
    AZURE_OPENAI_DEPLOYMENT   text-embedding-3-small

Two properties matter more than anything else here, and most of this file exists
to defend them:

  Every vector ends up on the chunk whose text produced it. Batching is the only
  reason that can go wrong, so it is enforced three times over - see
  `embed_texts` below.

  A failed run changes nothing. Vectors are collected in full and only then
  written onto the chunks, so a batch failing halfway through cannot leave a
  list where the first 60 chunks are embedded and the rest are not.

Logging follows the rule the connectors follow: one INFO line per *round trip*,
logged before it is made, so a stalled request is attributable to a numbered
batch rather than to silence. That is one line per batch of 30 - fifteen lines
for a 441-chunk repository, not 441. **Chunk content is never logged at any
level**, which is the rule confluence_storage and slack_parser follow for page
bodies and message text, and it matters the same way here: this is somebody's
private source code. Neither is the API key, which is not logged, not put in a
URL, and not folded into any message a client sees.
"""

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

# Reads the .env file next to the project root, and does nothing at all if the
# variables are already set in the environment. Called at import so a developer
# running uvicorn from a shell with no exports still gets a working service.
load_dotenv()

# How many chunk contents go into one embedding request. Fixed rather than
# configurable: it is small enough that one rejected request costs little and
# large enough that a 441-chunk repository takes 15 calls instead of 441.
EMBEDDING_BATCH_SIZE = 30

# The width every returned vector must have. This has to equal
# EMBEDDING_DIMENSIONS in app/entities/chunks/chunk.py, which pins the database
# column to vector(1536) - a wider vector from a swapped deployment would be
# rejected by the insert, and this catches it several steps earlier. Not
# imported from there on purpose: nothing in the ingestion pipeline imports
# app/entities, and a test asserts the two agree.
EMBEDDING_DIMENSIONS = 1536

# Roughly 8k tokens of code. The model's own input limit is per item, so one
# oversized chunk would fail the whole batch of 30 with it; truncating the
# *input* keeps the other 29 alive. `chunk.content` itself is never touched -
# what is stored stays an exact slice of the original file.
MAX_EMBEDDING_INPUT_CHARS = 24_000

# Transient failures only - a throttle or a dropped connection. A rejected
# request is not retried, because sending the same bad batch again just costs
# time before the same failure.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


class EmbeddableChunk(Protocol):
    """What this module needs from a chunk: text in, vector out.

    CodeChunk, JiraChunk, ConfluenceChunk and SlackChunk all satisfy this
    without inheriting anything - which is the point. The four pipelines are
    deliberately independent (see docs/architecture.md), so the one thing they
    genuinely share is written down as a shape rather than imposed as a base
    class. Nothing here knows or cares which source a chunk came from.
    """

    content: str
    embedding: list[float] | None
    embedding_model: str | None


class EmbeddableResult(Protocol):
    """What `embed_into` needs from an ingestion result.

    Every pipeline's result dataclass already carries these five: the chunks it
    produced, and the four numbers describing what embedding them took.
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

    # Chunks whose text was longer than one request may carry. They are still
    # embedded - on their first MAX_EMBEDDING_INPUT_CHARS - and still counted.
    truncated_inputs: int = 0


class ChunkEmbedder:
    """Embeds CodeChunk content against an OpenAI-compatible endpoint.

    Constructing one costs nothing: no environment variable is read, no client
    is built and nothing is contacted until the first batch is sent. That is
    what lets `github_routes` build the service at import time on a machine with
    no credentials, and what keeps every test that does not embed offline.
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

    # ----------------------------------------------------------------- public

    def embed_chunks(self, chunks: Sequence[EmbeddableChunk]) -> EmbeddingRun:
        """Embed every chunk and attach the vectors to those same objects.

        Takes chunks from any source - code, issues, pages or messages. It reads
        `content` and writes `embedding` and `embedding_model`, and there is
        nothing else it could do differently for one source than another.

        The chunks are modified in place - this is deliberately not a function
        that returns new chunks, because everything upstream already holds
        references to these and a copy would leave two versions of the truth.

        Nothing is written until every batch has come back and been checked, so
        a failure leaves all of them exactly as they were.
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

        # The whole-run check, after the per-batch ones. If this ever fires the
        # per-batch checks have a hole in them, and attaching vectors anyway
        # would put the wrong code next to the wrong embedding.
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

        # Only now is anything written. strict=True because a length mismatch
        # here would mean the check above is wrong, and zip's default would
        # silently truncate rather than say so.
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

        Order survives three separate hazards. The batches are slices of the
        caller's list, so nothing is reordered on the way out. Each response is
        re-sorted by the `index` the API reports rather than trusted to arrive
        in order. And a batch that comes back with the wrong number of vectors
        raises instead of shifting every later chunk by one.
        """
        if not texts:
            return []

        # Built here rather than on the first request, so a deployment that is
        # not configured fails before the run announces work it cannot do - and
        # so the line naming the deployment reads before the batches, not
        # between them.
        self._ensure_client()

        collected: list[list[float]] = []
        total_batches = _batch_count(len(texts), self.batch_size)

        # The counterpart to "Downloading 98 files from GitHub": what is about
        # to happen, and how many round trips it will take, before any of them
        # is made. One embedding request is the unit that costs time here.
        logger.info(
            "Embedding %d chunks in %d request(s) of at most %d",
            len(texts),
            total_batches,
            self.batch_size,
        )

        for number, start in enumerate(range(0, len(texts), self.batch_size), start=1):
            batch = texts[start : start + self.batch_size]

            # Before the request, not after - the same reason the connector logs
            # a file name before downloading it.
            logger.info(
                "[%d/%d] embedding %d chunks (%d characters)",
                number,
                total_batches,
                len(batch),
                sum(len(text) for text in batch),
            )

            items = self._request(batch)

            if len(items) != len(batch):
                # Logged as well as raised, and with the batch number the
                # message cannot carry: this is the failure that would have
                # silently misaligned every later chunk, so it is worth being
                # able to find in a log afterwards.
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

    # ---------------------------------------------------------------- private

    def _request(self, batch: list[str]) -> list:
        """Send one batch, retrying only what is worth retrying.

        The upstream error text is logged and never folded into the message the
        client sees - the same rule the connectors follow, and the reason an
        API key echoed back in a provider's error body cannot reach a response.
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
                # A rejected request: a bad deployment name, an oversized
                # payload, a revoked key. Sending it again would fail the same
                # way, so this one does not retry.
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

        Deliberately not done in __init__: importing this module, or wiring the
        service into the API, must not require credentials to be present.
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
        # Logged so an operator can see which endpoint was used. The key is not
        # part of the URL and is not logged anywhere in this module.
        logger.info("Embedding with deployment %s", deployment)
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        return self._client


# ------------------------------------------------------- the pipeline stage


def embed_into(
    result: EmbeddableResult,
    embedder: ChunkEmbedder | None,
    *,
    embed: bool = True,
) -> None:
    """Embed a run's chunks and record what that took on the result.

    This is the embedding stage of every pipeline, in one place. GitHub, Jira,
    Confluence and Slack each call it with their own result object and get
    identical behaviour, because there is nothing about embedding that differs
    between a TypeScript method and a Slack message.

    Three ways to do nothing, each of them normal: no embedder was configured,
    the caller asked to skip it, or the run produced no chunks. Each says so in
    the log rather than going quiet.

    Unlike parsing, this is all-or-nothing. A file that will not parse costs
    that one file; a batch of embeddings that comes back wrong cannot be
    attributed to a single chunk at all, so the run fails and the caller retries
    rather than storing a corpus that is 90% searchable.
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

    # The closing line, shaped like the connectors' "Downloaded 97 files,
    # skipped 1 (102 GitHub API calls)". The request count is the number worth
    # having when a later 429 needs explaining, and the model and width are what
    # say *which* vectors this corpus now holds.
    logger.info(
        "Embedded %d chunks into %d-dimension vectors with %s "
        "(%d embedding API calls) in %.1fs",
        run.embedded,
        run.dimensions,
        run.model,
        run.batches,
        time.monotonic() - started,
    )


# ------------------------------------------------------------------ helpers


def _clip(content: str) -> tuple[str, bool]:
    """Return the text to embed, and whether it had to be shortened."""
    if len(content) <= MAX_EMBEDDING_INPUT_CHARS:
        return content, False
    return content[:MAX_EMBEDDING_INPUT_CHARS], True


def _batch_count(items: int, size: int) -> int:
    """How many requests `items` takes at `size` per request."""
    return (items + size - 1) // size
