"""Protect TeX from Markdown and render it offline through MathJax + Qt SVG."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import base64
import xml.etree.ElementTree as ET
from uuid import uuid4

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQml import QJSEngine, QJSValue
from PySide6.QtSvg import QSvgRenderer


@dataclass(frozen=True)
class Formula:
    marker: str
    source: str
    tex: str
    display: bool
    complete: bool


# Code is consumed before math, so literal TeX examples stay literal. An open
# fence protects the remaining stream until its closing fence arrives.
_TOKEN = re.compile(
    r"(?P<indent>^(?: {4}|\t)[^\n]*(?:\n|$))"
    r"|(?P<link>\]\((?:\\.|[^()\\\n]|\([^()\n]*\))*(?:\)|$)|^ {0,3}\[[^\]\n]+\]:[^\n]*|<[^>\n]+>)"
    r"|(?P<fence>^ {0,3}(?P<fence_marks>`{3,}|~{3,})[^\n]*(?:\n|$))"
    r"|(?P<code>`+)"
    r"|(?P<math>\\\[|\\\(|\$\$|\$)", re.MULTILINE,
)


def protect_formulas(source: str):
    """Return Markdown with opaque math markers, including unfinished TeX.

    The original source is retained for errors, streaming and clipboard copies.
    Both common dollar delimiters and LaTeX bracket delimiters are supported.
    """
    prefix = "RENACLIPMATH" + uuid4().hex.upper()
    pieces, formulas, position = [], [], 0
    while match := _TOKEN.search(source, position):
        start, end = match.span()
        pieces.append(source[position:start])
        if match.group("indent") or match.group("link"):
            pieces.append(source[start:end])
            position = end
            continue
        if match.group("fence"):
            marks = match.group("fence_marks")
            closing = re.search(r"^ {0,3}" + re.escape(marks[0]) + "{" + str(len(marks)) + r",}[ \t]*(?:\n|$)", source[end:], re.MULTILINE)
            stop = end + closing.end() if closing else len(source)
            pieces.append(source[start:stop])
            position = stop
            continue
        if match.group("code"):
            marks = match.group("code")
            closing = re.search(r"(?<!`)" + re.escape(marks) + r"(?!`)", source[end:])
            stop = end + closing.end() if closing else end
            pieces.append(source[start:stop])
            position = stop
            continue
        # A delimiter preceded by an odd number of slashes is escaped.
        slashes = len(source[:start]) - len(source[:start].rstrip("\\"))
        opening = match.group("math")
        if slashes % 2 or (opening == "$" and (end == len(source) or source[end].isspace())):
            pieces.append(opening)
            position = end
            continue
        closing = {r"\[": r"\]", r"\(": r"\)", "$$": "$$", "$": "$"}[opening]
        stop = end
        found = -1
        while (stop := source.find(closing, stop)) != -1:
            preceding = len(source[:stop]) - len(source[:stop].rstrip("\\"))
            if preceding % 2 == 0 and not (opening == "$" and (source[stop - 1].isspace() or source[stop:stop + 2] == "$$")):
                found = stop
                break
            stop += len(closing)
        display = opening in (r"\[", "$$")
        # A single-dollar inline expression cannot extend into a new paragraph.
        limit = source.find("\n\n", end) if opening == "$" else -1
        if limit >= 0 and (found < 0 or limit < found):
            pieces.append(opening)
            position = end
            continue
        complete = found >= 0
        stop = found + len(closing) if complete else len(source)
        tex = source[end:found if complete else stop]
        marker = prefix + str(len(formulas)) + "END"
        formulas.append(Formula(marker, source[start:stop], tex, display, complete))
        # Keep block/list/quote context intact; Markdown recognizes a marker
        # on its own source line as an ordinary paragraph in that container.
        pieces.append(marker)
        position = stop
    pieces.append(source[position:])
    return "".join(pieces), formulas


def qt_compatible_svg(svg, pixels_per_unit):
    """Rasterize nested viewports only: Qt SVG Tiny skips nested SVG/clips.

    MathJax uses these viewports to crop stretched brackets/accents. Rendering
    each as a standalone SVG preserves its viewBox crop before embedding it
    back as a PNG at the exact same coordinates and device resolution.
    """
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.fromstring(svg)

    def flatten(parent):
        for index, child in enumerate(list(parent)):
            if child.tag == "{" + namespace + "}path" and not child.get("d", "").strip():
                # U+2061 FUNCTION APPLICATION is intentionally invisible.
                parent.remove(child)
                continue
            flatten(child)
            if child.tag != "{" + namespace + "}svg":
                continue
            attrs = dict(child.attrib)
            width, height = float(attrs["width"]), float(attrs["height"])
            if max(width, height) * pixels_per_unit > 16384:
                raise ValueError("Nested formula exceeds image budget")
            child.attrib.pop("x", None)
            child.attrib.pop("y", None)
            # Explicit pixel dimensions prevent Qt from deriving a viewport
            # from the ex-sized parent; viewBox supplies the glyph coordinates.
            child.set("width", str(max(1, round(width * pixels_per_unit))))
            child.set("height", str(max(1, round(height * pixels_per_unit))))
            image = QImage(max(1, round(width * pixels_per_unit)), max(1, round(height * pixels_per_unit)), QImage.Format.Format_ARGB32_Premultiplied)
            if image.sizeInBytes() > 64 * 1024 * 1024:
                raise ValueError("Nested formula exceeds image budget")
            image.fill(Qt.GlobalColor.transparent)
            renderer = QSvgRenderer(QByteArray(ET.tostring(child)))
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            encoded = base64.b64encode(bytes(data)).decode("ascii")
            replacement = ET.Element("{" + namespace + "}image", {
                "x": attrs.get("x", "0"), "y": attrs.get("y", "0"),
                "width": str(width), "height": str(height),
                "preserveAspectRatio": "none",
                "{http://www.w3.org/1999/xlink}href": "data:image/png;base64," + encoded,
            })
            if "transform" in attrs:
                replacement.set("transform", attrs["transform"])
            replacement.tail = child.tail
            index = list(parent).index(child)
            parent.remove(child)
            parent.insert(index, replacement)

    flatten(root)
    return ET.tostring(root)


class FormulaRenderer:
    """One lazy JS engine per browser, bounded SVG image cache, no network/DOM."""
    def __init__(self):
        self._engine = None
        self._cache = OrderedDict()

    def _initialize(self):
        engine = QJSEngine()
        bundle = Path(__file__).with_name("math_assets") / "renderer.bundle.js"
        result = engine.evaluate(bundle.read_text(encoding="utf-8"), str(bundle))
        if result.isError():
            raise RuntimeError(result.toString())
        self._engine = engine

    def render(self, tex, display, color, font_pixels, ratio):
        if len(tex) > 16384:
            raise ValueError("Formula exceeds input budget")
        key = (tex, display, color, font_pixels, ratio)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if self._engine is None:
            self._initialize()
        function = self._engine.globalObject().property("renderFormula")
        value = function.call([QJSValue(tex), QJSValue(display)])
        if value.isError():
            raise ValueError(value.toString())
        result = json.loads(value.toString())
        if result.get("error"):
            raise ValueError(result["error"])
        svg = result["svg"].replace("currentColor", color)
        if 'data-mml-node="merror"' in svg or 'data-mjx-error' in svg:
            raise ValueError("Invalid or unsupported TeX")
        if re.search(r'<(?:image|foreignObject|script|style)\b|(?:href|src)\s*=\s*[\"\'](?!#)', svg, re.IGNORECASE):
            raise ValueError("Formula SVG references external content")
        viewbox = re.search(r'viewBox="([^"]+)"', svg)
        if not viewbox:
            raise ValueError("MathJax SVG has no viewBox")
        _, _, width_units, height_units = map(float, viewbox.group(1).split())
        # MathJax uses 1000 SVG units per em, matching the text font size.
        width = max(1, width_units * font_pixels / 1000)
        height = max(1, height_units * font_pixels / 1000)
        if width * height * ratio * ratio > 16_000_000 or max(width, height) * ratio > 16384:
            raise ValueError("Formula exceeds image budget")
        compatible = qt_compatible_svg(svg, font_pixels * ratio / 1000)
        renderer = QSvgRenderer(QByteArray(compatible))
        if not renderer.isValid():
            raise ValueError("Invalid formula SVG")
        image = QImage(max(1, round(width * ratio)), max(1, round(height * ratio)), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        image.setDevicePixelRatio(ratio)
        rendered = (image, width, height)
        self._cache[key] = rendered
        while len(self._cache) > 128 or sum(item[0].sizeInBytes() for item in self._cache.values()) > 32 * 1024 * 1024:
            self._cache.popitem(last=False)
        return rendered
