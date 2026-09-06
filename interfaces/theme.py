"""Shared dark/pink design tokens for Flet and the native screenshot overlay.

Keep palette and dimensions here; toolkit-specific adapters consume these values.
Flet is imported lazily so the native overlay does not need it for its theme.
"""

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


def apply_flet_theme(page):
    """Apply one explicit dark theme to the page and all its dialogs."""
    import flet as ft

    shape = ft.RoundedRectangleBorder(radius=RADIUS)
    text_style = ft.TextStyle(font_family=FONT_FAMILY, size=BODY_SIZE,
                              color=TEXT, letter_spacing=0)
    theme = ft.Theme(
        color_scheme_seed=ACCENT,
        font_family=FONT_FAMILY,
        use_material3=True,
        color_scheme=ft.ColorScheme(
            primary=ACCENT_LIGHT, on_primary=BACKGROUND,
            primary_container=SURFACE_ACCENT, on_primary_container=ACCENT_LIGHT,
            secondary=ACCENT_LIGHT, on_secondary=BACKGROUND,
            secondary_container=SURFACE_ACCENT, on_secondary_container=TEXT,
            tertiary=ACCENT_LIGHT, on_tertiary=BACKGROUND,
            tertiary_container=SURFACE_RAISED, on_tertiary_container=TEXT,
            surface=SURFACE, on_surface=TEXT, on_surface_variant=TEXT_MUTED,
            surface_dim=BACKGROUND, surface_bright=SURFACE_RAISED,
            surface_container_lowest=BACKGROUND, surface_container_low=SURFACE,
            surface_container=SURFACE, surface_container_high=SURFACE_RAISED,
            surface_container_highest=SURFACE_RAISED, surface_tint=SURFACE,
            outline=OUTLINE, outline_variant=DIVIDER,
            error=ERROR, on_error=BACKGROUND,
        ),
        scaffold_bgcolor=BACKGROUND, canvas_color=SURFACE, card_bgcolor=SURFACE,
        divider_color=DIVIDER, disabled_color=DISABLED, hint_color=TEXT_MUTED,
        text_theme=ft.TextTheme(body_medium=text_style, body_large=text_style),
        card_theme=ft.CardTheme(color=SURFACE, elevation=0, shape=shape),
        dialog_theme=ft.DialogTheme(
            bgcolor=SURFACE, shape=shape,
            title_text_style=ft.TextStyle(color=TEXT, size=20, font_family=FONT_FAMILY,
                                         weight=ft.FontWeight.W_600, letter_spacing=0),
            content_text_style=text_style,
        ),
        filled_button_theme=ft.FilledButtonTheme(style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: ACCENT, ft.ControlState.DISABLED: SURFACE_RAISED},
            color={ft.ControlState.DEFAULT: TEXT, ft.ControlState.DISABLED: DISABLED},
            shape=shape,
        )),
        outlined_button_theme=ft.OutlinedButtonTheme(style=ft.ButtonStyle(
            color={ft.ControlState.DEFAULT: ACCENT_LIGHT, ft.ControlState.DISABLED: DISABLED},
            shape=shape,
        )),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color={ft.ControlState.DEFAULT: ACCENT, ft.ControlState.HOVERED: ACCENT_LIGHT,
                         ft.ControlState.DRAGGED: ACCENT_LIGHT},
            track_color=SURFACE, track_border_color=SURFACE,
            thickness=SCROLLBAR_WIDTH, radius=RADIUS, interactive=True,
        ),
    )
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = theme
    page.dark_theme = theme
    page.bgcolor = BACKGROUND


def configure_tk_scrollbar(style, name="Screenshot.Vertical.TScrollbar"):
    """Use painted scrollbars so Windows does not override the shared palette."""
    style.theme_use("clam")
    style.layout(name, [
        ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"sticky": "nswe", "expand": "1"}),
        ]}),
    ])
    style.configure(name, troughcolor=SURFACE, background=ACCENT,
                    darkcolor=ACCENT, lightcolor=ACCENT, bordercolor=SURFACE,
                    borderwidth=0, relief="flat", width=SCROLLBAR_WIDTH)
    style.map(name, background=[("active", ACCENT_LIGHT), ("pressed", ACCENT_LIGHT)])
    return name
