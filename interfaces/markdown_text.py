"""Markdown rendering for the native screenshot answer Text widget."""
from __future__ import annotations

from dataclasses import dataclass
import re
import webbrowser
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from interfaces.theme import ACCENT_LIGHT, FONT_FAMILY, TEXT, SURFACE_RAISED


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    tags: tuple[str, ...]
    href: str | None = None


@dataclass(frozen=True)
class MarkdownDocument:
    text: str
    spans: tuple[TextSpan, ...]


_MD = MarkdownIt("commonmark", {"html": False})
_LINK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _safe_href(value: str | None) -> str | None:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        return None
    if "\\" in value or not _LINK_RE.match(value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _attrs(token, key: str):
    attrs = token.attrs or {}
    return attrs.get(key) if isinstance(attrs, dict) else dict(attrs).get(key)


def parse_markdown(source: str) -> MarkdownDocument:
    """Parse CommonMark into plain text and formatting/link spans."""
    source = str(source or "")
    tokens = _MD.parse(source)
    out: list[str] = []
    spans: list[TextSpan] = []
    stack: list[tuple[str, str | None]] = []

    length = 0

    def add(value: str, href: str | None = None):
        nonlocal length
        if not value:
            return
        start = length
        length += len(value)
        out.append(value)
        tags = tuple(tag for tag, _ in stack)
        link = href or next((item_href for tag, item_href in reversed(stack) if tag == "link"), None)
        if tags or link:
            spans.append(TextSpan(start, start + len(value), tags, link))

    def inline(children):
        for child in children or []:
            typ = child.type
            if typ == "text":
                add(child.content)
            elif typ == "code_inline":
                stack.append(("code", None)); add(child.content); stack.pop()
            elif typ == "softbreak":
                add("\n")
            elif typ == "hardbreak":
                add("\n")
            elif typ == "image":
                add(child.content or _attrs(child, "alt") or "")
            elif typ == "link_open":
                stack.append(("link", _safe_href(_attrs(child, "href"))))
            elif typ == "link_close":
                if stack and stack[-1][0] == "link": stack.pop()
            elif typ.endswith("_open"):
                tag = {"strong_open": "bold", "em_open": "italic"}.get(typ)
                if tag: stack.append((tag, None))
            elif typ.endswith("_close"):
                tag = {"strong_close": "bold", "em_close": "italic"}.get(typ)
                if tag and stack and stack[-1][0] == tag: stack.pop()
            elif child.content:
                add(child.content)

    block_seen = False
    for token in tokens:
        typ = token.type
        if typ == "inline":
            if block_seen and out and not out[-1].endswith("\n"):
                add("\n")
            inline(token.children)
            block_seen = True
        elif typ in ("heading_open", "paragraph_open", "blockquote_open", "list_item_open", "bullet_list_open", "ordered_list_open"):
            if typ in ("heading_open", "blockquote_open", "list_item_open") and out and not out[-1].endswith("\n"):
                add("\n")
        elif typ in ("heading_close", "paragraph_close", "blockquote_close", "list_item_close", "bullet_list_close", "ordered_list_close"):
            if out and not out[-1].endswith("\n"):
                add("\n")
        elif typ == "fence":
            if out and not out[-1].endswith("\n"): add("\n")
            stack.append(("code", None)); add(token.content.rstrip("\n")); stack.pop(); add("\n")
        elif typ == "code_block":
            if out and not out[-1].endswith("\n"): add("\n")
            stack.append(("code", None)); add(token.content.rstrip("\n")); stack.pop(); add("\n")
        elif typ == "hr":
            if out and not out[-1].endswith("\n"): add("\n")
            add("\n")
    text = "".join(out).rstrip("\n")
    spans = tuple(span for span in spans if span.start < len(text))
    return MarkdownDocument(text, spans)


class TkMarkdownRenderer:
    """Render Markdown through Text insert segments, avoiding Tcl/non-BMP offsets."""
    def __init__(self, widget, opener=webbrowser.open_new_tab):
        self.widget = widget
        self.opener = opener
        self.document = None
        self.pixel_size = None
        self._link_tags: dict[str, str] = {}
        self._bound = False
        self._hover_tag = None
        self._bind_events()

    def _bind_events(self):
        self.widget.bind("<Button-1>", self._on_click, add=True)
        self.widget.bind("<Motion>", self._on_motion, add=True)
        self.widget.bind("<Leave>", self._on_leave, add=True)
        self._bound = True

    def render(self, document: MarkdownDocument, pixel_size: int) -> bool:
        if document == self.document and pixel_size == self.pixel_size:
            return False
        try:
            view = self.widget.yview()
        except Exception:
            view = None
        try:
            selection = tuple(str(index) for index in self.widget.tag_ranges("sel"))
        except Exception:
            selection = ()
        preserve_selection = self.document is not None and document.text.startswith(self.document.text)
        self.widget.configure(state="normal")
        self.widget.delete("1.0", "end")
        for tag in ("bold", "italic", "code", "link") + tuple(self._link_tags):
            try: self.widget.tag_delete(tag)
            except Exception: pass
        self._link_tags.clear()
        self.widget.configure(font=(FONT_FAMILY, -max(8, int(pixel_size))), fg=TEXT,
                              insertbackground=TEXT)
        for tag, opts in (("bold", {"font": (FONT_FAMILY, -max(8, int(pixel_size)), "bold")}),
                          ("italic", {"font": (FONT_FAMILY, -max(8, int(pixel_size)), "italic")}),
                          ("code", {"font": ("Consolas", -max(8, int(pixel_size))), "background": SURFACE_RAISED}),
                          ("link", {"foreground": ACCENT_LIGHT, "underline": True})):
            self.widget.tag_configure(tag, **opts)
        pos = 0
        for span in document.spans:
            if span.start > pos: self.widget.insert("end", document.text[pos:span.start])
            tags = list(span.tags)
            if span.href:
                tag = f"mdlink_{len(self._link_tags)}"; self._link_tags[tag] = span.href; tags.append("link"); tags.append(tag)
            self.widget.insert("end", document.text[span.start:span.end], tuple(dict.fromkeys(tags)))
            pos = span.end
        if pos < len(document.text): self.widget.insert("end", document.text[pos:])
        if preserve_selection and len(selection) == 2:
            self.widget.tag_add("sel", *selection)
        self.widget.configure(state="disabled")
        self.document = document
        self.pixel_size = pixel_size
        if view:
            try:
                if view[1] >= 0.995:
                    self.widget.see("end")
                else:
                    self.widget.yview_moveto(view[0])
            except Exception:
                pass
        return True

    def _link_at(self, event):
        try:
            index = self.widget.index(f"@{event.x},{event.y}")
            box = self.widget.bbox(index)
            if not box or not (box[0] <= event.x < box[0] + box[2] and box[1] <= event.y < box[1] + box[3]):
                return None
            tags = self.widget.tag_names(index)
        except Exception:
            return None
        return next((self._link_tags[tag] for tag in tags if tag in self._link_tags), None)

    def _on_click(self, event):
        href = self._link_at(event)
        if href:
            try: self.opener(href)
            except Exception: pass
            return "break"
        return None

    def _on_motion(self, event):
        tag = self._link_at(event)
        try: self.widget.configure(cursor="hand2" if tag else "xterm")
        except Exception: pass
        return None

    def _on_leave(self, event):
        try: self.widget.configure(cursor="xterm")
        except Exception: pass
        return None
