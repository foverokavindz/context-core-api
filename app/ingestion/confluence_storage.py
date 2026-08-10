"""Confluence storage format -> plain text.

Confluence Cloud returns page bodies as "storage format": XHTML-like markup that
mixes ordinary HTML with Confluence's own namespaced elements. None of it
belongs in a chunk, so this module flattens the markup into readable text before
the body crosses the ConfluencePage boundary.

    <h2>Authentication</h2>                    Authentication
    <p>TrackIt uses JWT.</p>            ->
    <ul><li>Log in</li></ul>                   TrackIt uses JWT.

                                               - Log in

This is a *flattening*, not a faithful renderer, and the difference is
deliberate. Bold and italic lose their emphasis; links keep their text and drop
their URL; headings lose their level; tables become pipe-separated rows. Contrast
CodeChunk.content, which guarantees an exact byte slice of the original file -
there is no such guarantee here, because what an embedding model needs from a
wiki page is the prose, not the markup.

Two rules carry most of the weight:

  Unknown elements are rendered by rendering their children. Confluence ships
  new macros constantly and third-party apps add their own, so ac:structured-
  macro, ac:rich-text-body, ac:link and every future tag are handled by not
  being handled - whatever readable text sits inside them survives, and nothing
  raises. There is deliberately no attempt to interpret a macro's meaning.

  Blocks and inline runs are distinguished by what a node produced, not by a
  hard-coded list. A <span> containing only text joins the sentence around it; a
  macro body containing paragraphs becomes paragraphs. That is decided during
  the walk, so it costs nothing extra and is right for tags this module has
  never heard of.

Pure functions only. Nothing here knows about ConfluencePage, and nothing here
performs I/O. This is the Confluence counterpart to jira_adf.py, and the two
share a normalisation pass by design - text from either source should look the
same by the time it reaches a chunk.
"""

import re

from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, PreformattedString, Tag

# Storage markup nests: a list inside a table cell inside a macro body inside an
# expand. Real pages go a handful of levels deep, so a limit two orders of
# magnitude above that turns a pathological document into truncated text instead
# of a RecursionError that would fail the whole ingestion.
MAX_STORAGE_DEPTH = 50

# Rendered as nothing at all. script and style are not content; ac:parameter is
# a macro's *configuration* - a chart's axis name or a table filter's column -
# and is noise rather than knowledge. The macro's actual body arrives separately
# in ac:rich-text-body or ac:plain-text-body, both of which are kept.
_IGNORED_TAGS = frozenset({"script", "style", "ac:parameter"})

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Whitespace inside these is significant and is passed through untouched.
# ac:plain-text-body is where the code macro keeps its source, wrapped in CDATA.
_VERBATIM_TAGS = frozenset({"pre", "ac:plain-text-body"})

# Block-level elements that render as nothing but still separate what surrounds
# them, so a rule between two paragraphs does not glue them together.
_EMPTY_BLOCK_TAGS = frozenset({"hr", "ac:image", "img"})


def storage_to_text(value: object) -> str:
    """Flatten a Confluence storage body into plain text.

    Accepts whatever Confluence actually sent. A missing body, a null one or one
    holding something that is not a string all become "" - an empty page is a
    valid page, not a parse failure. This never raises: BeautifulSoup recovers
    from malformed markup rather than rejecting it, and every unrecognised tag
    falls through to a generic path.
    """
    if not isinstance(value, str) or not value:
        return ""

    # html.parser is Python's own, so this adds no compiled dependency. It also
    # preserves namespaced names verbatim - "ac:structured-macro" arrives as
    # exactly that - which is what makes the generic fallback below work.
    soup = BeautifulSoup(value, "html.parser")

    text, _ = _render_children(soup, depth=0)
    return _normalise(text)


# --------------------------------------------------------------- traversal


def _render_node(node: object, *, depth: int) -> tuple[str, bool]:
    """Render one node, and say whether it came out as a block.

    The boolean is what lets the caller decide between joining with a blank line
    and running text together. It is derived rather than declared, so an unknown
    wrapper is treated as a block exactly when its contents made it one.
    """
    if depth > MAX_STORAGE_DEPTH:
        return "", False

    if isinstance(node, NavigableString):
        if isinstance(node, PreformattedString) and not isinstance(node, CData):
            # Comments, doctypes and processing instructions. CData is the one
            # exception: it is where a code macro keeps its source.
            return "", False
        # Storage format is XHTML, so the newlines and indentation between tags
        # are formatting rather than content. Runs inside verbatim elements are
        # never reached here - _render_verbatim handles those.
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

    # Everything else, which is most of what a real page contains: a, strong,
    # em, code, span, div, ac:structured-macro, ac:rich-text-body, ac:link,
    # ri:page, and whatever Confluence adds next. Render the children and let
    # what they produced decide how this reads.
    return _render_children(node, depth=depth)


def _render_children(
    node: Tag | BeautifulSoup, *, depth: int, separator: str = "\n\n"
) -> tuple[str, bool]:
    """Render a node's children, grouping runs of inline text into blocks.

    Inline pieces are concatenated with no separator, because "Hello " and
    "world" are one sentence rather than two lines. A block child ends the run
    in progress and stands on its own.
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

    Code is the whole reason this path exists. Collapsing the newlines out of a
    code block would leave a single unreadable line, so nothing here touches the
    spacing - and _normalise only ever strips trailing whitespace and collapses
    runs of *blank* lines, so indentation survives it too.
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
        # A single newline between an item's blocks, not a blank one: a bullet
        # and the sub-list under it should read as one item rather than drifting
        # apart.
        body, _ = _render_children(item, depth=depth + 1, separator="\n")
        if not body:
            continue
        marker = f"{number}. " if ordered else "- "
        lines.append(_prefix(body, marker, " " * len(marker)))
        number += 1

    return "\n".join(lines)


def _prefix(body: str, marker: str, continuation: str) -> str:
    """Mark the first line of an item and indent the rest under it.

    This is also what indents nested lists, without any depth arithmetic: an
    inner list renders as "- Inner" on its own, and its parent item's
    continuation prefix turns that into "  - Inner".
    """
    lines = body.split("\n")
    return "\n".join(
        [marker + lines[0]]
        + [continuation + line if line else line for line in lines[1:]]
    )


def _render_table(node: Tag, *, depth: int) -> str:
    """Render a table as pipe-separated rows.

    Deliberately not a Markdown table: no alignment row, no padding, no column
    widths. What matters for retrieval is that "API" and "8000" stay on the same
    line as each other and under the right headings, and this does that in a
    tenth of the code a real renderer would take.
    """
    rows: list[str] = []

    # Descendant rather than direct-child search, because storage format wraps
    # rows in <tbody>. The ancestor check is what keeps a nested table's rows
    # out of the outer table, where they would otherwise appear twice.
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

    Character for character the same pass jira_adf._normalise runs, so a chunk
    built from a wiki page and one built from an issue description are spaced
    the same way. Note what it does *not* do: it never touches leading
    whitespace on a line, which is what lets code blocks and nested list
    indentation through unharmed.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
