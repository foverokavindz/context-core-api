"""ConfluencePage - one wiki page, normalised into our own shape.

This model is THE boundary of the Confluence pipeline, the counterpart to
JiraIssue on the Jira side and RepositoryFile on the GitHub side. Everything
upstream of it may know about Confluence's REST API, its field names and its
storage-format markup; everything downstream of it - the chunker - must not.

By the time a page is a ConfluencePage, `content` is plain readable text. No
storage XHTML survives this boundary: no <p>, no <h2>, no ac:structured-macro.

The field list is deliberately short. Confluence returns an author, an owner, a
created timestamp, labels, properties and a full version history, and none of
them earn a place in a chunk yet. `version_number` is the one piece of
provenance kept, because it is what a future incremental sync would compare -
though nothing in this version does that.
"""

from pydantic import BaseModel


class ConfluencePage(BaseModel):
    """A single Confluence page, flattened to text."""

    page_id: str

    # Both come from the space the run resolved, not from the page payload.
    # Every page in one run therefore reports the same space, which is the
    # containment guarantee this pipeline is built around.
    space_id: str
    space_key: str

    # Free from the space lookup that already had to happen, so it costs no
    # extra request. Optional because a space can be resolved without a name.
    space_name: str | None = None

    title: str

    # The page above this one in the space's tree, as Confluence reported it.
    # Kept even when that page was not part of this ingestion - a dangling
    # pointer is more useful than a silently dropped one, and it is exactly what
    # a capped run produces. Unlike Jira, no reverse `child_pages` list is
    # built: nothing downstream needs one, and it would cost a pass to invent.
    parent_id: str | None = None

    # "current" for everything this pipeline ingests. Recorded rather than
    # asserted, so a server that ignores the status filter is visible in the
    # response instead of silently trusted.
    status: str | None = None

    version_number: int | None = None

    # Plain text, flattened from the storage body. A page with an empty body is
    # still a valid page and gets "" here.
    content: str
