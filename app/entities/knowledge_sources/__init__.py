"""Knowledge source entities. Importing this package registers both mappers."""

from app.entities.knowledge_sources.chunk import Chunk
from app.entities.knowledge_sources.chunk_type import ChunkType
from app.entities.knowledge_sources.resource import Resource
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.entities.knowledge_sources.resource_type import ResourceType

__all__ = [
    "Chunk",
    "ChunkType",
    "Resource",
    "ResourceAccessScope",
    "ResourceType",
]
