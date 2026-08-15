"""Chunk entities. Importing this package registers the chunk mapper."""

from app.entities.chunks.chunk import Chunk
from app.entities.chunks.chunk_type import ChunkType

__all__ = ["Chunk", "ChunkType"]
