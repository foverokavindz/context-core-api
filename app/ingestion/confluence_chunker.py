import logging

from app.models.confluence.chunk import ConfluenceChunk
from app.models.confluence.page import ConfluencePage

logger = logging.getLogger(__name__)

# Stands in for an empty page body so a chunk never ends on a dangling
# "Content:" header with nothing under it. Confluence pages are routinely
# created empty - a placeholder in a tree, or a parent that only exists to hold
# children - so this is a normal outcome rather than a failure.
NO_CONTENT_TEXT = "(no content)"


class ConfluenceChunker:
    """Renders pages as the text we expect to embed."""

    def chunk(self, page: ConfluencePage) -> ConfluenceChunk:
        """Render one page.
        """
        return ConfluenceChunk(
            page_id=page.page_id,
            space_id=page.space_id,
            space_key=page.space_key,
            space_name=page.space_name,
            title=page.title,
            parent_id=page.parent_id,
            status=page.status,
            version_number=page.version_number,
            content=self._render_content(page),
            external_id=page.external_id,
        )

    def chunk_many(self, pages: list[ConfluencePage]) -> list[ConfluenceChunk]:
        """Render every page, in order.
        """
        chunks = [self.chunk(page) for page in pages]
        logger.info("Generated %d Confluence chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(page: ConfluencePage) -> str:
        """Lay out one page as plain readable text.
        """
        lines = [
            f"Space: {_space_label(page)}",
            f"Page: {page.title}",
            "",
            "Content:",
            page.content or NO_CONTENT_TEXT,
        ]

        return "\n".join(lines)


def _space_label(page: ConfluencePage) -> str:
    """Name the space the way a person would.
    """
    if page.space_name:
        return f"{page.space_name} ({page.space_key})"
    return page.space_key
