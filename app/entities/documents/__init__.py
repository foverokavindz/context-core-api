"""Document entities. Importing this package registers the document mapper."""

from app.entities.documents.document import Document
from app.entities.documents.document_status import DocumentStatus

__all__ = ["Document", "DocumentStatus"]
