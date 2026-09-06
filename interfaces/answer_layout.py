"""Pure, bounded sizing helpers for a growing screenshot answer bubble.

All rectangles and measured widths/heights are physical pixels. Scale is DPI/96.
Prefer Text.count(..., 'displaylines') + 1 for exact mounted Tk line counts;
measure_wrapped_lines provides a bounded first-pass estimate before rendering.
"""

import math

MIN_ANSWER_HEIGHT = 90
MAX_ANSWER_HEIGHT = 560
MAX_PANEL_HEIGHT = 720


def _scale(scale):
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    return scale


def measure_wrapped_lines(text, width, measure, *, max_lines=128, max_chars=16384):
    """Estimate word-wrapped lines using cached glyph widths, with bounded work.

    Explicit newlines are preserved, words move together where possible, and
    CJK/overwide words split by character. Glyph summation is approximate (font
    kerning may differ from Tk); the mounted Text widget is the exact authority.
    At either budget limit, return max_lines so unusually large input expands to
    the height cap rather than spending time measuring invisible overflow.
    """
    if not math.isfinite(width) or width <= 0:
        raise ValueError("width must be finite and positive")
    if max_lines < 1 or max_chars < 1:
        raise ValueError("measurement budgets must be positive")
    lines, used, word = 1, 0.0, 0.0
    have_break = False
    glyphs = {}
    previous_cr = False
    for index, character in enumerate(text):
        if index >= max_chars:
            return max_lines
        if character == "\n" and previous_cr:
            previous_cr = False
            continue
        previous_cr = character == "\r"
        if character in "\n\r":
            lines += 1
            used = word = 0.0
            have_break = False
        else:
            if character not in glyphs:
                glyphs[character] = max(0.0, float(measure("    " if character == "\t" else character)))
            glyph = glyphs[character]
            if character.isspace():
                word = 0.0
                have_break = True
            else:
                word += glyph
            if used > 0 and used + glyph > width:
                lines += 1
                used = word if have_break and 0 < word <= width else glyph
                word = used if not character.isspace() else 0.0
                have_break = False
            else:
                used += glyph
        if lines >= max_lines:
            return max_lines
    return lines


def answer_content_height(display_lines, line_height, scale=1.0):
    """Add physical header and padding to text height; output capped for layout.

    line_height is physical pixels (Tk Font.metrics('linespace')); do not multiply
    it by scale again. A 48-logical-pixel allowance covers heading and padding.
    """
    _scale(scale)
    if display_lines < 0 or not math.isfinite(line_height) or line_height <= 0:
        raise ValueError("line count must be nonnegative and line height positive")
    return min(math.ceil(MAX_ANSWER_HEIGHT * scale),
               math.ceil(max(1, display_lines) * line_height + 48 * scale))


def compute_answer_bounds(question_bounds, panel_max_bounds, content_height, scale=1.0):
    """Grow an answer below a fixed question, capped to available screen space.

    panel_max_bounds must already be constrained to the monitor's work area.
    Heights range from90 to560 logical pixels unless less space is available.
    Returns None when there is no usable width/height below the question. The
    question is never moved, and the gap shrinks only for extremely small areas.
    content_height includes answer text, heading, and interior padding.
    """
    _scale(scale)
    if not math.isfinite(content_height) or content_height < 0:
        raise ValueError("content_height must be finite and nonnegative")
    pl, pt, pr, pb = panel_max_bounds
    ql, qt, qr, qb = question_bounds
    if pr <= pl or pb <= pt or qr <= ql or qb <= qt:
        raise ValueError("rectangles must have positive dimensions")
    left, right = max(pl, ql), min(pr, qr)
    base = max(pt, qb)
    if right <= left or pb <= base:
        return None
    gap = min(max(1, round(10 * scale)), max(0, pb - base - 1))
    top = base + gap
    desired = max(math.ceil(MIN_ANSWER_HEIGHT * scale), math.ceil(content_height))
    height = min(desired, math.floor(MAX_ANSWER_HEIGHT * scale), pb - top)
    return left, top, right, top + height
