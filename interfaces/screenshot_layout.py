"""Pure Qt logical-pixel placement for independently anchored screenshot bubbles."""
from PySide6.QtCore import QRect


def _validate(area, width, height, gap):
    if area.isEmpty():
        raise ValueError('work_area must have positive dimensions')
    if width <= 0 or height <= 0 or gap < 0:
        raise ValueError('dimensions must be positive and gap nonnegative')


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def place_question(selection: QRect, work_area: QRect, width=360, height=96, gap=12) -> QRect:
    """Anchor beside the selection without reserving any future answer height.

    Prefer right then left. If neither fits, prefer the larger usable vertical
    strip below/above. Only an unavoidable full-area selection overlaps the box.
    Inputs are never mutated; negative desktop origins are supported.
    """
    _validate(work_area, width, height, gap)
    if selection.isEmpty():
        raise ValueError('selection must have positive dimensions')
    width, height = min(int(width), work_area.width()), min(int(height), work_area.height())
    gap = int(gap)
    left, top = work_area.x(), work_area.y()
    right, bottom = left + work_area.width(), top + work_area.height()
    sx, sy = selection.x(), selection.y()
    sr, sb = sx + selection.width(), sy + selection.height()
    y = _clamp(sy, top, bottom - height)
    if sr + gap + width <= right and sr + gap >= left:
        return QRect(sr + gap, y, width, height)
    if sx - gap - width >= left and sx - gap <= right:
        return QRect(sx - gap - width, y, width, height)
    x = _clamp(sx, left, right - width)
    below = max(0, bottom - max(top, sb + gap))
    above = max(0, min(bottom, sy - gap) - top)
    spaces = [('below', below), ('above', above)]
    fitting = [entry for entry in spaces if entry[1] >= height]
    direction, available = max(fitting or spaces, key=lambda entry: entry[1])
    if available > 0:
        actual_height = min(height, available)
        y = max(top, sb + gap) if direction == 'below' else min(bottom, sy - gap) - actual_height
        return QRect(x, y, width, actual_height)
    return QRect(x, y, width, height)


def place_answer(question: QRect, work_area: QRect, desired_height: int,
                 max_height=560, gap=10, preferred_direction=None) -> tuple[QRect, str]:
    """Grow around a fixed question, using below/above before a side fallback.

    A preferred direction is kept when it still fits the requested height. If
    neither vertical strip fits, use the larger one, except that strips under
    90 pixels yield to a usable right/left strip (at least 180 pixels wide).
    An empty QRect means there is no non-overlapping space at all; callers can
    hide the answer rather than move the question or cover it.
    """
    _validate(work_area, question.width(), desired_height, gap)
    if question.isEmpty() or max_height <= 0:
        raise ValueError('question and max_height must have positive dimensions')
    width = min(question.width(), work_area.width())
    height = min(int(desired_height), int(max_height), work_area.height())
    gap = int(gap)
    left, top = work_area.x(), work_area.y()
    right, bottom = left + work_area.width(), top + work_area.height()
    qx, qy = question.x(), question.y()
    qr, qb = qx + question.width(), qy + question.height()
    below_top = max(top, qb + gap)
    above_bottom = min(bottom, qy - gap)
    spaces = {'below': max(0, bottom - below_top),
              'above': max(0, above_bottom - top)}
    x = _clamp(qx, left, right - width)

    def vertical(direction):
        actual_height = min(height, spaces[direction])
        if actual_height <= 0:
            return QRect(), direction
        y = below_top if direction == 'below' else above_bottom - actual_height
        return QRect(x, y, width, actual_height), direction

    if preferred_direction in spaces and spaces[preferred_direction] >= height:
        return vertical(preferred_direction)
    for direction in ('below', 'above'):
        if spaces[direction] >= height:
            return vertical(direction)

    if max(spaces.values()) < 90:
        side_spaces = {'right': max(0, right - max(left, qr + gap)),
                       'left': max(0, min(right, qx - gap) - left)}
        usable = [direction for direction in ('right', 'left')
                  if side_spaces[direction] >= min(width, 180)]
        if usable:
            if preferred_direction in usable:
                direction = preferred_direction
            else:
                direction = max(usable, key=lambda side: min(width, side_spaces[side]))
            actual_width = min(width, side_spaces[direction])
            sx = max(left, qr + gap) if direction == 'right' else min(right, qx - gap) - actual_width
            sy = _clamp(qy, top, bottom - height)
            return QRect(sx, sy, actual_width, height), direction

    direction = max(('below', 'above'), key=lambda item: spaces[item])
    return vertical(direction)
