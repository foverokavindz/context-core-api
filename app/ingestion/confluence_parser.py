
import logging

from app.ingestion.confluence_storage import storage_to_text
from app.models.confluence.page import ConfluencePage

logger = logging.getLogger(__name__)

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
        """
        page_id = _identifier(raw.get("id"))
        if page_id is None:
            raise ValueError("Confluence page payload has no id.")

        version_number = _version_number(raw.get("version"))

        return ConfluencePage(
            page_id=page_id,
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
    """
    return value if isinstance(value, dict) else {}


def _identifier(value: object) -> str | None:
    """A Confluence id as a string, or None.
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
    """
    number = _as_dict(value).get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        return number
    return None


def _body_text(value: object) -> str:
    """Read body.storage.value and flatten it to text.
    """
    storage = _as_dict(_as_dict(value).get("storage"))
    return storage_to_text(storage.get("value"))
