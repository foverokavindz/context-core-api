
import re

from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, PreformattedString, Tag

MAX_STORAGE_DEPTH = 50

_IGNORED_TAGS = frozenset({"script", "style", "ac:parameter"})

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_VERBATIM_TAGS = frozenset({"pre", "ac:plain-text-body"})

_EMPTY_BLOCK_TAGS = frozenset({"hr", "ac:image", "img"})


def storage_to_text(value: object) -> str:
    """Flatten a Confluence storage body into plain text.
    """
    if not isinstance(value, str) or not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")

    text, _ = _render_children(soup, depth=0)
    return _normalise(text)


# traversal
def _render_node(node: object, *, depth: int) -> tuple[str, bool]:
    """Render one node, and say whether it came out as a block.
    """
    if depth > MAX_STORAGE_DEPTH:
        return "", False

    if isinstance(node, NavigableString):
        if isinstance(node, PreformattedString) and not isinstance(node, CData):
            return "", False

        return re.sub(r"\s+", " ", str(node)), False

    if not isinstance(node, Tag):
        return "", False

    name = node.name or ""

    if name in _IGNORED_TAGS:
        return "", False

    if name == "br":
        return "\n", False

    if name in _EMPTY_BLOCK_TAGS:
        return "", True

    if name in _VERBATIM_TAGS:
        return _render_verbatim(node), True

    if name == "p" or name in _HEADING_TAGS:
        text, _ = _render_children(node, depth=depth)
        return text, True

    if name == "blockquote":
        text, _ = _render_children(node, depth=depth)
        return text, True

    if name == "ul":
        return _render_list(node, ordered=False, depth=depth), True

    if name == "ol":
        return _render_list(node, ordered=True, depth=depth), True

    if name == "table":
        return _render_table(node, depth=depth), True

    return _render_children(node, depth=depth)


def _render_children(
    node: Tag | BeautifulSoup, *, depth: int, separator: str = "\n\n"
) -> tuple[str, bool]:
    """Render a node's children, grouping runs of inline text into blocks.
    """
    blocks: list[str] = []
    inline: list[str] = []
    saw_block = False

    def flush() -> None:
        """Close the inline run in progress, if it held anything readable."""
        run = "".join(inline).strip()
        inline.clear()
        if run:
            blocks.append(run)

    for child in node.children:
        text, is_block = _render_node(child, depth=depth + 1)
        if is_block:
            saw_block = True
            flush()
            if text:
                blocks.append(text)
        else:
            inline.append(text)

    flush()
    return separator.join(blocks), saw_block


def _render_verbatim(node: Tag) -> str:
    """Return a preformatted element's text with its whitespace intact.
    """
    return node.get_text()


def _render_list(node: Tag, *, ordered: bool, depth: int) -> str:
    """Render a bullet or numbered list, one item per line."""
    start = 1
    if ordered:
        attr = node.get("start")
        if isinstance(attr, str) and attr.strip().lstrip("-").isdigit():
            start = int(attr)

    lines: list[str] = []
    number = start

    for item in node.find_all("li", recursive=False):

        body, _ = _render_children(item, depth=depth + 1, separator="\n")
        if not body:
            continue
        marker = f"{number}. " if ordered else "- "
        lines.append(_prefix(body, marker, " " * len(marker)))
        number += 1

    return "\n".join(lines)


def _prefix(body: str, marker: str, continuation: str) -> str:
    """Mark the first line of an item and indent the rest under it.
    """
    lines = body.split("\n")
    return "\n".join(
        [marker + lines[0]]
        + [continuation + line if line else line for line in lines[1:]]
    )


def _render_table(node: Tag, *, depth: int) -> str:
    """Render a table as pipe-separated rows.
    """
    rows: list[str] = []

    for row in node.find_all("tr"):
        if row.find_parent("table") is not node:
            continue

        cells: list[str] = []
        for cell in row.find_all(["th", "td"], recursive=False):
            text, _ = _render_children(cell, depth=depth + 1, separator=" ")
            cells.append(" ".join(text.split()))

        if any(cells):
            rows.append(" | ".join(cells))

    return "\n".join(rows)


# ------------------------------------------------------------ normalisation


def _normalise(text: str) -> str:
    """Tidy the assembled text into something worth embedding.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
