"""Shared dark/pink design tokens and Qt widget styling."""
BACKGROUND = "#19171d"
SURFACE = "#242329"
SURFACE_RAISED = "#302b33"
SURFACE_ACCENT = "#30252e"
ACCENT = "#c2185b"
ACCENT_LIGHT = "#f48fb1"
TEXT = "#f3edf1"
TEXT_MUTED = "#c0b4bf"
OUTLINE = "#786773"
DIVIDER = "#443943"
DISABLED = "#847581"
ERROR = "#ffb4ab"
FONT_FAMILY = "Segoe UI"
BODY_SIZE = 14
RADIUS = 8
SPACING = 8
CONTENT_PADDING = 12
SCROLLBAR_WIDTH = 10
SELECTION_WIDTH = 4


def qt_stylesheet():
    return f"""
    QWidget {{ color: {TEXT}; font-family: '{FONT_FAMILY}'; font-size: {BODY_SIZE}px; }}
    QMainWindow, QDialog {{ background: {BACKGROUND}; }}
    QMenu, QToolTip {{ background: {SURFACE}; border: 1px solid {OUTLINE}; padding: 6px; }}
    QMenu::item:selected {{ background: {SURFACE_ACCENT}; color: {ACCENT_LIGHT}; }}
    QLabel {{ background: transparent; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QListWidget {{
        background: {SURFACE}; border: 1px solid {DIVIDER}; border-radius: {RADIUS}px;
        padding: 8px; selection-background-color: {ACCENT}; selection-color: {TEXT};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT_LIGHT}; }}
    QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {ACCENT}; }}
    QListWidget::item {{ padding: 8px; margin: 3px; border-radius: {RADIUS}px; }}
    QListWidget::item:selected {{ background: {SURFACE_ACCENT}; }}
    QPushButton {{ background: {SURFACE_ACCENT}; color: {ACCENT_LIGHT};
        border: 1px solid {OUTLINE}; border-radius: {RADIUS}px; padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {ACCENT_LIGHT}; background: {SURFACE_RAISED}; }}
    QPushButton:default {{ background: {ACCENT}; color: {TEXT}; border-color: {ACCENT}; }}
    QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {{ color: {DISABLED}; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {OUTLINE}; border-radius: 3px; background: {SURFACE}; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border: 2px solid {ACCENT_LIGHT}; }}
    QScrollBar:vertical {{ background: {SURFACE}; width: {SCROLLBAR_WIDTH}px; margin: 0; border-radius: 5px; }}
    QScrollBar::handle:vertical {{ background: {ACCENT}; min-height: 24px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: {ACCENT_LIGHT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def apply_qt_theme(app):
    from PySide6.QtGui import QFont, QPalette, QColor
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, 10))
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BACKGROUND), (QPalette.ColorRole.Base, SURFACE),
                        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Text, TEXT),
                        (QPalette.ColorRole.Button, SURFACE_ACCENT), (QPalette.ColorRole.ButtonText, TEXT),
                        (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, TEXT)):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(qt_stylesheet())
