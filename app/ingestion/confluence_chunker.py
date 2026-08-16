"""ConfluencePage -> ConfluenceChunk.

    ConfluencePage  ->  ConfluenceChunker  ->  ConfluenceChunk

One page produces exactly one chunk. No splitting by headings, tokens,
characters, paragraphs or sections - the simplest baseline that can work, so that
what a chunk contains is obvious before any smarter strategy is layered on. Wiki
pages are longer than Jira descriptions and this is the pipeline most likely to
outgrow the rule, but a baseline is what a heading-based strategy would have to
be measured against, and measuring it is not part of this version. When that
changes, the decision belongs here and nowhere else.

This module is where all chunk formatting lives. The route does not build chunk
text and neither does the connector, so the shape of what gets embedded is
readable in one place.

The one rule worth stating twice: ids stay out of the text. `page_id`,
`parent_id` and `version_number` all ride on the chunk as metadata, because they
are what a retrieval layer filters and deduplicates on - but a version number is
not something a reader asks a question about, and putting it in the prose would
only dilute what the page actually says.
"""

import logging

from app.models.confluence_chunk import ConfluenceChunk
from app.models.confluence_page import ConfluencePage

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

        The page's own fields are carried onto the chunk as metadata as well as
        being rendered into `content`, so a chunk is self-describing once it
        leaves the pipeline.
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

        len(chunks) == len(pages), always. Nothing here can skip a page or emit
        two for one, which is what makes `generated_chunks` and `parsed_pages`
        comparable in the response.
        """
        chunks = [self.chunk(page) for page in pages]
        logger.info("Generated %d Confluence chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(page: ConfluencePage) -> str:
        """Lay out one page as plain readable text.

        Three things, in the order a reader would want them: which space this
        came from, what the page is called, and what it says. The space and the
        title are the context an embedding cannot recover from the body - a
        paragraph about "the token endpoint" means something different in
        TrackIt than in PayFlow.

        Absent values are omitted rather than rendered as "None".
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

    "TrackIt (TR)" reads better than either half alone, and the key is worth
    keeping alongside the name because it is what a caller typed to get here.
    Falls back to the key when no name was resolved.
    """
    if page.space_name:
        return f"{page.space_name} ({page.space_key})"
    return page.space_key
