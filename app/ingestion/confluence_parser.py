"""Raw Confluence page JSON -> ConfluencePage.

    {"id": "111", "title": "Auth",        ->   ConfluencePage(page_id="111",
     "body": {"storage": {...}}}                              content="TrackIt...")

This is the only module that knows Confluence's field names. The connector knows
Confluence's endpoints and hands back whatever JSON came off the wire; everything
downstream sees only ConfluencePage. Nothing here performs I/O - the parser never
calls Confluence, which is why a page's body has to have been requested up front
with body-format=storage rather than fetched on demand here.

The space is passed in rather than read from the page. The connector already
resolved it, the answer is the same for every page in a run, and taking it from
the resolved space is what makes "every page in this response belongs to the
space you asked for" true by construction instead of by inspection.

Every extraction is defensive. Confluence omits fields an account cannot see,
returns null where a value is unset, and returns a page body only when one was
asked for, so nothing here indexes into a nested dict without checking what it
found. A payload with no usable id is the single case that raises; the caller
records it and moves on.
"""

import logging

from app.ingestion.confluence_storage import storage_to_text
from app.models.confluence.page import ConfluencePage

logger = logging.getLogger(__name__)

# One raw page exactly as the REST API returned it. `object` rather than `Any`
# because every read below narrows with isinstance anyway, and `object` is what
# forces that to stay true.
ConfluencePageJson = dict[str, object]


class ConfluenceParser:
    """Turns Confluence's page payloads into our own model."""

    def parse(
        self,
        raw: ConfluencePageJson,
        *,
        space_key: str,
        space_id: str,
        space_name: str | None = None,
    ) -> ConfluencePage:
        """Normalise one raw page.

        Raises ValueError when the payload carries no page id. Without an id a
        page cannot be identified, deduplicated or traced back to Confluence, so
        there is nothing useful to salvage - and a silently invented id would be
        worse. Everything else degrades: a page with no body, no version and no
        title is still a page, and is still worth returning.
        """
        page_id = _identifier(raw.get("id"))
        if page_id is None:
            raise ValueError("Confluence page payload has no id.")

        version_number = _version_number(raw.get("version"))

        return ConfluencePage(
            page_id=page_id,
            # The page reports a spaceId too, but the resolved space is what
            # this run is *about*; preferring it means a surprise from the API
            # cannot quietly widen the run past the space that was asked for.
            space_id=space_id,
            space_key=space_key,
            space_name=space_name,
            title=_text(raw.get("title")),
            parent_id=_identifier(raw.get("parentId")),
            status=_optional_text(raw.get("status")),
            version_number=version_number,
            content=_body_text(raw.get("body")),

            external_id=page_id,
            version_key=None if version_number is None else str(version_number),
        )

    def parse_many(
        self,
        raw_pages: list[ConfluencePageJson],
        errors: list[tuple[str, str]],
        *,
        space_key: str,
        space_id: str,
        space_name: str | None = None,
    ) -> list[ConfluencePage]:
        """Normalise every page, recording the ones that could not be read.

        One malformed payload costs one page, never the run - the same contract
        the Jira pipeline gives an issue with no key, and the GitHub pipeline a
        file that will not decode.
        """
        pages: list[ConfluencePage] = []

        for position, raw in enumerate(raw_pages, start=1):
            try:
                pages.append(
                    self.parse(
                        raw,
                        space_key=space_key,
                        space_id=space_id,
                        space_name=space_name,
                    )
                )
            except ValueError:
                # No id means nothing to name it by, so it is recorded by its
                # position in the response instead.
                logger.warning(
                    "Skipping Confluence page %d: payload has no id", position
                )
                errors.append(
                    (
                        f"page #{position}",
                        "Confluence returned a page with no id.",
                    )
                )

        logger.info("Parsed %d Confluence pages", len(pages))
        return pages


# --------------------------------------------------------------- extraction


def _as_dict(value: object) -> dict[str, object]:
    """A nested object, or an empty one.

    Covers a missing key, an explicit null, and a value of the wrong type in a
    single place, so no caller has to guard each of those separately.
    """
    return value if isinstance(value, dict) else {}


def _identifier(value: object) -> str | None:
    """A Confluence id as a string, or None.

    Confluence v2 returns ids as JSON strings, but the same ids appear as
    numbers elsewhere in Atlassian's APIs and a proxy could reshape one. An int
    is accepted and stringified so a page is not thrown away over its id's JSON
    type; anything else - including the null that every root page has for
    parentId - becomes None.
    """
    if isinstance(value, str):
        return value.strip() or None
    # bool is a subclass of int, and "True" is not an id.
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _text(value: object) -> str:
    """A trimmed string field, or an empty string."""
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    """A trimmed string field, or None when there was nothing to trim."""
    return _text(value) or None


def _version_number(value: object) -> int | None:
    """Read version.number, tolerating the version object being absent.

    Kept as provenance for a future incremental sync. Nothing in this version
    compares it, so an unreadable one costs nothing and is simply dropped.
    """
    number = _as_dict(value).get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        return number
    return None


def _body_text(value: object) -> str:
    """Read body.storage.value and flatten it to text.

    Written as three guarded lookups rather than one chain because each level is
    genuinely optional: `body` is absent unless body-format was requested,
    `storage` is absent if a different representation came back, and `value` is
    absent on a page with an empty body. A page missing any of them is not an
    error - it is a page with no content.
    """
    storage = _as_dict(_as_dict(value).get("storage"))
    return storage_to_text(storage.get("value"))
