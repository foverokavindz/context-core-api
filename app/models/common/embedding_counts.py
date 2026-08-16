from pydantic import BaseModel


class EmbeddingCounts(BaseModel):
    """What one run's embedding pass produced."""

    chunks: int
    embeddings: int

    embedding_batches: int

    embedding_model: str | None = None
    embedding_dimensions: int | None = None

    truncated_inputs: int = 0
