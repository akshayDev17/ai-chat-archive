"""Turn a DIL-v2 reply back into the GenUI markup the archive stores.

Since 2026-11 chatgpt.com serves shares from its "octane" web app, which ships
each GenUI reply as a small compiled JavaScript program instead of the markup
the model wrote::

    DIL.render(__dil.jsx(()=>{const __dilConstants=DIL.useConstants();...
      return __dil.jsx(__dil.Fragment,null,
        __dil.jsx("title",{"size":"lg"},__dilConstants["0"]),
        __dil.jsx("text",null,__dilConstants["1"],
          __dilSafe(()=>(__dil.jsx(Cite,{"refs":__dilConstants["2"],
                                         "__resolutionId":__dilConstants["3"]})),null)),
        ...);},{"key":"body:0"}));

The program is a tree of ``__dil.jsx(type, props, ...children)`` calls over a
constants table. :func:`render_dil` parses that tree — reading it, never
evaluating it — and writes it back out as Markdown with GenUI tags
(``<box>``, ``<Cite refs={…}/>``, ``<AsyncImage …/>``), which is what the old
share page carried and what the parser and the reader already understand.

It also returns where each data-bound component landed, in the shape of the
old ``metadata.genui_components``, so the parser can attach a ``<Cite/>``'s
sources and an image component's pictures by offset.

Anything outside the small grammar the share pages use raises
:class:`UnsupportedDil`; the caller then keeps the page's own Markdown
rendering of the reply, which has the prose but none of the components.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


class UnsupportedDil(ValueError):
    """The DIL program uses a construct this reader does not know."""


# -- the parsed tree -----------------------------------------------------------


@dataclass
class Element:
    type: str
    props: dict
    children: list = field(default_factory=list)


@dataclass
class Function:
    """An arrow function, e.g. ``onClick:()=>GenUI.issueNewTurn("…")``. Never run."""


# Parser intermediates; none of them survives into the element tree.
@dataclass
class _Name:
    name: str


@dataclass
class _Call:
    name: str


@dataclass
class _Arrow:
    body: object


_FRAGMENT = "__dil.Fragment"
_JSX = "__dil.jsx"
_SAFE = "__dilSafe"
_CONSTANTS = "__dilConstants"

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<arrow>=>)
  | (?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)
  | (?P<punct>[()\[\]{},:;])
    """,
    re.VERBOSE,
)


def _tokens(code: str, start: int) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = start
    while pos < len(code):
        match = _TOKEN_RE.match(code, pos)
        if match is None:
            raise UnsupportedDil(f"unexpected character {code[pos]!r}")
        pos = match.end()
        kind = match.lastgroup
        if kind != "space":
            tokens.append((kind, match.group()))
    return tokens


class _Parser:
    """Recursive descent over the expression subset DIL programs use."""

    def __init__(self, tokens: list[tuple[str, str]], constants: dict):
        self._tokens = tokens
        self._pos = 0
        self._constants = constants

    def _peek(self) -> tuple[str, str]:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else ("end", "")

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        self._pos += 1
        return token

    def _expect(self, value: str) -> None:
        kind, text = self._next()
        if text != value:
            raise UnsupportedDil(f"expected {value!r}, got {text!r}")

    def expression(self) -> object:
        kind, text = self._next()
        if kind == "string":
            try:
                return json.loads(text)
            except ValueError as exc:
                raise UnsupportedDil("string literal is not JSON") from exc
        if kind == "number":
            return json.loads(text)
        if text == "[":
            return self._list("]")
        if text == "{":
            return self._object()
        if text == "(":
            if self._peek()[1] == ")":
                self._next()
                self._expect("=>")
                self._arrow_body()
                return Function()
            value = self.expression()
            self._expect(")")
            return value
        if kind == "name":
            return self._name(text)
        raise UnsupportedDil(f"unexpected token {text!r}")

    def _arrow_body(self) -> object:
        if self._peek()[1] == "{":
            raise UnsupportedDil("arrow function with a statement body")
        return self.expression()

    def _name(self, name: str) -> object:
        if name in ("true", "false"):
            return name == "true"
        if name == "null":
            return None
        if name == _CONSTANTS:
            self._expect("[")
            key = self.expression()
            self._expect("]")
            if not isinstance(key, str) or key not in self._constants:
                raise UnsupportedDil(f"unknown constant {key!r}")
            return self._constants[key]
        if self._peek()[1] != "(":
            # A bare identifier: only meaningful as a component type.
            return _Name(name)
        self._next()
        args = self._list(")")
        if name == _JSX:
            return self._element(args)
        if name == _SAFE:
            if not args or not isinstance(args[0], _Arrow):
                raise UnsupportedDil("__dilSafe without a function")
            return args[0].body
        return _Call(name)

    def _list(self, close: str) -> list:
        items: list = []
        while self._peek()[1] != close:
            if self._peek()[1] == "(" and self._is_arrow():
                items.append(self._arrow())
            else:
                items.append(self.expression())
            if self._peek()[1] == ",":
                self._next()
            elif self._peek()[1] != close:
                raise UnsupportedDil(f"expected ',' or {close!r}")
        self._next()
        return items

    def _is_arrow(self) -> bool:
        return (
            self._pos + 2 < len(self._tokens)
            and self._tokens[self._pos + 1][1] == ")"
            and self._tokens[self._pos + 2][1] == "=>"
        )

    def _arrow(self) -> "_Arrow":
        self._expect("(")
        self._expect(")")
        self._expect("=>")
        return _Arrow(self._arrow_body())

    def _object(self) -> dict:
        out: dict = {}
        while self._peek()[1] != "}":
            kind, key = self._next()
            if kind == "string":
                key = json.loads(key)
            elif kind != "name":
                raise UnsupportedDil(f"bad object key {key!r}")
            self._expect(":")
            if self._peek()[1] == "(" and self._is_arrow():
                self._arrow()
                out[key] = Function()
            else:
                out[key] = self.expression()
            if self._peek()[1] == ",":
                self._next()
            elif self._peek()[1] != "}":
                raise UnsupportedDil("expected ',' or '}'")
        self._next()
        return out

    def _element(self, args: list) -> Element:
        if len(args) < 2:
            raise UnsupportedDil("__dil.jsx needs a type and props")
        kind, props, children = args[0], args[1], args[2:]
        if isinstance(kind, _Name):
            kind = kind.name
        if not isinstance(kind, str):
            raise UnsupportedDil("element type is not a name")
        if props is None:
            props = {}
        if not isinstance(props, dict):
            raise UnsupportedDil("element props are not an object")
        for child in children:
            if isinstance(child, (_Name, _Call, _Arrow, Function)):
                raise UnsupportedDil("element child is not content")
        return Element(kind, props, [c for c in children if c is not None])


def parse_dil(code: str, constants: dict) -> Element:
    """The element tree a DIL program renders."""
    start = code.find("DIL.render(")
    if start < 0 or code.count("DIL.render(") != 1:
        raise UnsupportedDil("expected exactly one DIL.render call")
    body = code.find(";return ", start)
    if body < 0:
        raise UnsupportedDil("render function has no return")
    parser = _Parser(_tokens(code, body + len(";return ")), constants)
    root = parser.expression()
    if not isinstance(root, Element):
        raise UnsupportedDil("render function does not return an element")
    return root


# -- writing markup ------------------------------------------------------------

#: Prose elements, written as Markdown. Everything else lowercase is a layout
#: tag (``box``, ``row``, ``icon``, ``divider``, …), written as markup.
_PROSE = {"text", "title", "list", "table", "blockquote"}
#: Containers the share page wraps around prose; they add nothing to it.
_TRANSPARENT = {_FRAGMENT, "col", "pressable"}
_HEADING = {"2xl": "# ", "xl": "# ", "lg": "## ", "md": "### ", "sm": "#### "}
#: GenUI components the parser resolves, by the type name the old payload used.
_COMPONENT_TYPES = {"Cite": "cite", "AsyncImage": "async_image", "AsyncImageGroup": "async_image_group"}
_INLINE_MARKS = {"bold": "**", "italic": "*", "code": "`"}
_ALIGN_RULE = {"end": "---:|", "center": ":---:|"}


class _Writer:
    def __init__(self) -> None:
        self._chunks: list[str] = []
        self.pos = 0
        self.components: list[dict] = []

    def write(self, text: str) -> None:
        self._chunks.append(text)
        self.pos += len(text)

    def text(self) -> str:
        return "".join(self._chunks)


def _attr(name: str, value: object) -> str | None:
    if isinstance(value, Function) or value is None or name.startswith("__"):
        return None
    if isinstance(value, bool):
        return name if value else None
    if isinstance(value, (int, float)):
        return f"{name}={{{json.dumps(value)}}}"
    if isinstance(value, str):
        return f"{name}={json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return f"{name}={{{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}}}"
    raise UnsupportedDil(f"attribute {name} has an unsupported value")


def _open_tag(element: Element, close: bool) -> str:
    attrs = [a for a in (_attr(k, v) for k, v in element.props.items()) if a]
    inner = " ".join([element.type, *attrs])
    return f"<{inner}/>" if close else f"<{inner}>"


def _component(out: _Writer, element: Element) -> None:
    """Write a data-bound component's tag and record where it sits."""
    start = out.pos
    out.write(_open_tag(element, close=True))
    resolution = element.props.get("__resolutionId")
    if element.type in _COMPONENT_TYPES and isinstance(resolution, str):
        out.components.append(
            {
                "type": _COMPONENT_TYPES[element.type],
                "start_index": start,
                "end_index": out.pos,
                "component_resolution_id": resolution,
            }
        )


#: ``lead`` for a table cell: one line, with its pipes escaped.
_CELL = None


def _text(text: str, lead: str | None) -> str:
    if lead is _CELL:
        return text.replace("\n", " ").replace("|", "\\|")
    return text.replace("\n", "\n" + lead)


def _inline(out: _Writer, children: list, lead: str | None, in_tag_line: bool = False) -> None:
    for child in children:
        if isinstance(child, str):
            out.write(_text(child, lead))
        elif isinstance(child, (int, float)) and not isinstance(child, bool):
            out.write(str(child))
        elif isinstance(child, Element):
            _inline_element(out, child, lead, in_tag_line)
        else:
            raise UnsupportedDil("inline child is not text")


def _inline_element(out: _Writer, element: Element, lead: str | None, in_tag_line: bool = False) -> None:
    if element.type in _INLINE_MARKS:
        mark = _INLINE_MARKS[element.type]
        out.write(mark)
        _inline(out, element.children, lead, in_tag_line)
        out.write(mark)
    elif element.type == "Link":
        _link(out, element, lead, in_tag_line)
    elif element.type[:1].isupper():
        _component(out, element)
    else:
        # `text`, `pressable`, … inside a line: their content is the line.
        _inline(out, element.children, lead, in_tag_line)


def _link(out: _Writer, element: Element, lead: str | None, in_tag_line: bool) -> None:
    """A link as Markdown — or, label-less inside a ``<text …>`` line, as the
    ``<Link url title/>`` tag the old markup used there."""
    url = element.props.get("url")
    if not isinstance(url, str):
        raise UnsupportedDil("Link without a url")
    title = element.props.get("title")
    if not element.children and in_tag_line:
        out.write(_open_tag(element, close=True))
        return
    out.write("[")
    if element.children:
        _inline(out, element.children, lead, in_tag_line)
    else:
        out.write(_text(title if isinstance(title, str) else url, lead))
    out.write(f"]({url})")


#: ``text`` props that only say how a line inside a pressable looks.
_PLAIN_TEXT_PROPS = {"inline", "underline"}


def _is_tag_line(node: Element, in_tag: bool) -> bool:
    """Whether ``node`` is written as one ``<tag …>line</tag>``, as the old
    markup wrote a styled ``text``, a ``title`` inside a layout tag, and a
    ``button``."""
    if node.type == "text":
        return bool(node.props.keys() - _PLAIN_TEXT_PROPS)
    return node.type == "button" or (node.type == "title" and in_tag)


def _is_prose(node: object, in_tag: bool) -> bool:
    if isinstance(node, str):
        return True
    return (
        isinstance(node, Element)
        and node.type in _PROSE
        and not _is_tag_line(node, in_tag)
    )


def _flatten(children: list) -> list:
    """Children with transparent containers replaced by their own children."""
    flat: list = []
    for child in children:
        if isinstance(child, Element) and child.type in _TRANSPARENT:
            flat.extend(_flatten(child.children))
        else:
            flat.append(child)
    return flat


def _blocks(out: _Writer, children: list, lead: str, in_tag: bool) -> None:
    previous = None
    for child in _flatten(children):
        if previous is not None:
            loose = not in_tag or (_is_prose(previous, in_tag) and _is_prose(child, in_tag))
            out.write(("\n" + lead.rstrip() + "\n" + lead) if loose else ("\n" + lead))
        _block(out, child, lead, in_tag)
        previous = child


def _block(out: _Writer, node: object, lead: str, in_tag: bool) -> None:
    if isinstance(node, str):
        _inline(out, [node], lead)
        return
    if not isinstance(node, Element):
        raise UnsupportedDil("block is not an element")
    kind = node.type
    if _is_tag_line(node, in_tag):
        out.write(_open_tag(node, close=False))
        _inline(out, node.children, lead, in_tag_line=True)
        out.write(f"</{kind}>")
    elif kind == "text":
        _inline(out, node.children, lead)
    elif kind == "title":
        size = node.props.get("size")
        out.write(_HEADING.get(size if isinstance(size, str) else "", "### "))
        _inline(out, node.children, lead)
    elif kind == "list":
        _list(out, node, lead, in_tag)
    elif kind == "table":
        _table(out, node, lead)
    elif kind == "blockquote":
        out.write("> ")
        _blocks(out, node.children, lead + "> ", in_tag=False)
    elif kind == "code":
        raise UnsupportedDil("block-level code")
    elif kind == "divider" and not in_tag:
        out.write("---")
    elif kind == "Link":
        _link(out, node, lead, in_tag_line=False)
    elif kind[:1].isupper():
        _component(out, node)
    else:
        _layout(out, node, lead)


def _layout(out: _Writer, node: Element, lead: str) -> None:
    if not node.children:
        out.write(_open_tag(node, close=True))
        return
    inner = lead + "  "
    out.write(_open_tag(node, close=False) + "\n" + inner)
    _blocks(out, node.children, inner, in_tag=True)
    out.write("\n" + lead + f"</{node.type}>")


def _list(out: _Writer, node: Element, lead: str, in_tag: bool) -> None:
    numbered = node.props.get("marker") == "number"
    if not all(isinstance(c, Element) for c in node.children):
        raise UnsupportedDil("list child is not an element")
    for n, item in enumerate(node.children, start=1):
        if n > 1:
            out.write("\n" + lead)
        marker = f"{n}. " if numbered else "- "
        out.write(marker)
        _blocks(out, item.children if item.type == "list-item" else [item],
                lead + " " * len(marker), in_tag)


def _cell(out: _Writer, cell: Element) -> None:
    for n, child in enumerate(cell.children):
        if n:
            out.write(" ")
        if isinstance(child, Element) and child.type == "text":
            _inline(out, child.children, _CELL)
        else:
            _inline(out, [child], _CELL)


def _table(out: _Writer, node: Element, lead: str) -> None:
    rows = node.children
    if not rows or not all(isinstance(r, Element) and r.type == "table-row" for r in rows):
        raise UnsupportedDil("table children are not rows")
    for n, row in enumerate(rows):
        cells = row.children
        if not all(isinstance(c, Element) for c in cells):
            raise UnsupportedDil("table cell is not an element")
        if n:
            out.write("\n" + lead)
        out.write("| ")
        for m, cell in enumerate(cells):
            if m:
                out.write(" | ")
            _cell(out, cell)
        out.write(" |")
        if n == 0:
            rule = "".join(_ALIGN_RULE.get(c.props.get("align"), "---|") for c in cells)
            out.write("\n" + lead + "|" + rule)


def render_dil(dil: dict) -> tuple[str, list[dict]]:
    """The reply as GenUI markup, and where its data-bound components sit."""
    code, constants = dil.get("code"), dil.get("constants")
    if not isinstance(code, str) or not isinstance(constants, dict):
        raise UnsupportedDil("DIL without code or constants")
    root = parse_dil(code, constants)
    out = _Writer()
    _blocks(out, [root], "", in_tag=False)
    return out.text(), out.components
