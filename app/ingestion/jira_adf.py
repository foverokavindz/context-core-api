"""Atlassian Document Format -> plain text.

Jira Cloud returns issue descriptions as ADF: a JSON tree of typed nodes. None
of that belongs in a chunk, so this module flattens the tree into readable text
before the description crosses the JiraIssue boundary.

    {"type": "doc", "content": [                     Employees can edit their
      {"type": "paragraph", "content": [       ->    own timesheet entries.
        {"type": "text", "text": "Employees..."}]}]}

This is a *flattening*, not a faithful renderer, and the difference is
deliberate. Formatting marks are dropped and the text kept; headings lose their
level; blockquotes lose their quoting; runs of blank lines collapse. Contrast
CodeChunk.content, which guarantees an exact byte slice of the original file -
there is no such guarantee here, because what an embedding model needs from a
Jira description is the prose, not the markup.

Pure functions only. Nothing here knows about JiraIssue, and nothing here
performs I/O.
"""

import re

# ADF nests: a list inside a list item inside a panel inside a table cell. Real
# documents go a handful of levels deep, so a limit two orders of magnitude
# above that turns a pathological or cyclic-looking payload into truncated text
# instead of a RecursionError that would fail the whole ingestion.
MAX_ADF_DEPTH = 50

# Block nodes whose children are inline runs: their text is concatenated with no
# separator, because "Hello " and "world" are one sentence, not two lines.
_INLINE_CONTAINERS = frozenset({"paragraph", "heading", "codeBlock"})

# Block nodes whose children are themselves blocks, separated by a blank line.
_BLOCK_CONTAINERS = frozenset({"doc", "blockquote"})


def adf_to_text(value: object) -> str:
    """Flatten an ADF document into plain text.

    Accepts whatever Jira actually sent. A description that arrives as a plain
    string - which older APIs and some webhook payloads still do - is returned
    unchanged rather than being normalised, so nothing is silently rewritten.
    Anything unrecognisable becomes an empty string; this never raises.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    return _normalise(_render_node(value, depth=0))


# --------------------------------------------------------------- traversal


def _render_node(node: object, *, depth: int) -> str:
    """Render one ADF node and everything below it."""
    if depth > MAX_ADF_DEPTH or not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    content = node.get("content")

    if node_type == "text":
        # Marks - bold, italic, links, code - are dropped and the text kept.
        text = node.get("text")
        return text if isinstance(text, str) else ""

    if node_type == "hardBreak":
        return "\n"

    if node_type in _INLINE_CONTAINERS:
        return _render_inline(content, depth=depth)

    if node_type in _BLOCK_CONTAINERS:
        return _render_blocks(content, depth=depth)

    if node_type == "bulletList":
        return _render_list(node, ordered=False, depth=depth)

    if node_type == "orderedList":
        return _render_list(node, ordered=True, depth=depth)

    if node_type == "listItem":
        # A single newline, not a blank line: one bullet's paragraphs should
        # read as one item rather than drifting apart.
        parts = [
            _render_node(child, depth=depth + 1) for child in _as_list(content)
        ]
        return "\n".join(part for part in parts if part)

    return _render_unknown(node, depth=depth)


def _render_unknown(node: dict[str, object], *, depth: int) -> str:
    """Salvage what text there is from a node type we do not recognise.

    Jira adds node types over time and third-party apps add their own, so an
    unknown type must never crash and should never silently drop text that is
    sitting right there. Three attempts, in order of how directly the text is
    attached.
    """
    text = node.get("text")
    if isinstance(text, str) and text:
        return text

    # Inline nodes carry their display text on attrs: a mention renders as
    # "@Alice", an emoji as ":smile:", a status lozenge as "DONE".
    attrs = node.get("attrs")
    if isinstance(attrs, dict):
        attr_text = attrs.get("text")
        if isinstance(attr_text, str) and attr_text:
            return attr_text

    # Panels, tables, expands, layouts: wrappers whose children are the point.
    return _render_blocks(node.get("content"), depth=depth)


def _render_inline(children: object, *, depth: int) -> str:
    """Join inline children with no separator - text runs are contiguous."""
    return "".join(
        _render_node(child, depth=depth + 1) for child in _as_list(children)
    )


def _render_blocks(children: object, *, depth: int) -> str:
    """Join block children with a blank line, dropping the ones that are empty.

    Dropping matters: an image or a rule renders as nothing, and without this it
    would leave a hole between the paragraphs around it.
    """
    parts = [_render_node(child, depth=depth + 1) for child in _as_list(children)]
    return "\n\n".join(part for part in parts if part)


def _render_list(node: dict[str, object], *, ordered: bool, depth: int) -> str:
    """Render a bullet or numbered list, one item per line."""
    start = 1
    attrs = node.get("attrs")
    if isinstance(attrs, dict) and isinstance(attrs.get("order"), int):
        start = attrs["order"]

    lines: list[str] = []
    for number, child in enumerate(_as_list(node.get("content")), start=start):
        body = _render_node(child, depth=depth + 1)
        if not body:
            continue
        marker = f"{number}. " if ordered else "- "
        lines.append(_prefix(body, marker, " " * len(marker)))

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


def _as_list(value: object) -> list[object]:
    """A node's children, or nothing.

    One helper covers three malformed shapes at once: a missing `content` key, a
    null one, and one holding something that is not a list.
    """
    return value if isinstance(value, list) else []


# ------------------------------------------------------------ normalisation


def _normalise(text: str) -> str:
    """Tidy the assembled text into something worth embedding."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
