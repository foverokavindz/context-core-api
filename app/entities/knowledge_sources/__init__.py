"""Knowledge source entities. Importing this package registers the resource mapper."""

from app.entities.knowledge_sources.resource import Resource
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.entities.knowledge_sources.resource_type import ResourceType

__all__ = [
    "Resource",
    "ResourceAccessScope",
    "ResourceType",
]
