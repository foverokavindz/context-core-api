"""The debug/verification response returned by the Jira ingest endpoint.

This response exists so a human can eyeball whether ingestion actually worked:
counts to check the funnel (retrieved -> epics/stories -> chunks), the
normalised issues themselves, a few sample chunks, and anything that was
skipped.

Two different things can shorten what you see here, and they must not be
confused:

  truncated: true   the INGESTION stopped early. Jira had more issues than we
                    fetched, because the issue cap was reached. Every count
                    below describes only what was retrieved.

  full: false       the RESPONSE is a window onto a complete run. The issue and
                    chunk lists are sampled and chunk text is shortened, but
                    every count is a true total and `truncated` is untouched.

Sampling never sets `truncated`.
"""

from pydantic import BaseModel

from app.models.jira_issue import JiraIssue

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_ISSUES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

# Sample chunks show the head of their text, not all of it. The JiraChunk
# objects themselves always keep the complete content - this cap applies only
# to what gets serialised into the HTTP response.
CHUNK_CONTENT_PREVIEW_CHARS = 600

# Ceiling on how many issues one request will retrieve. Jira pages at 100
# issues per call, so this is five calls rather than an unbounded walk of a
# large project. When the cap is hit, `truncated` is set.
MAX_ISSUES_PER_INGESTION = 500


class JiraChunkSample(BaseModel):
    """One generated chunk, with its text possibly shortened for display."""

    key: str
    issue_type: str
    summary: str
    status: str | None = None
    parent_key: str | None = None
    content: str


class JiraIssueError(BaseModel):
    """An issue that was skipped, or a note about the run, and why.

    Keyed by issue key where one issue is at fault, and by project key for a
    note about the run as a whole. Neither is fatal - a single unparseable
    payload should not cost you the other 200 issues.
    """

    issue: str
    reason: str


class JiraIngestResponse(BaseModel):
    """What the Jira ingest endpoint returns."""

    site_url: str
    project_key: str

    # The funnel. retrieved -> classified as epics/stories -> chunks produced.
    # For this version generated_chunks equals retrieved_issues unless an issue
    # failed to parse.
    retrieved_issues: int
    epics: int
    stories: int
    generated_chunks: int

    # True when the issue cap stopped the run before Jira ran out of results,
    # meaning this is a partial view of the project. Not affected by sampling.
    truncated: bool = False

    # The normalised issues, sampled unless `full` was requested. These are the
    # whole model rather than a summary: checking that descriptions arrived as
    # plain text and that parent/child links resolved is the main reason to
    # look at this response at all.
    issues: list[JiraIssue] = []

    sample_chunks: list[JiraChunkSample] = []
    errors: list[JiraIssueError] = []
