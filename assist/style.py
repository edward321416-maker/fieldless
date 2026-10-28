"""The web app's look, for the other screens that show a survey (docs/ui-rules.md U2, U3, U12): the HTML report.

An item's colour comes from its place in the scan's item list (the standard's checks, then the stage items:
server.views.items_json), and its icon from the shape name its standard gives it (`map_icon`). No item is named
here: the icons are shapes, so a new item in a standard file needs no code. The palette and the shapes are the web
app's own (web/src/items.ts, web/src/icons.ts); tests/assist/test_style.py fails when the two drift apart.
"""
from html import escape

# Colour-blind safe (Okabe-Ito and neighbours), no red or orange: red marks a possible defect, orange a site check.
PALETTE = ("#0072B2", "#009E73", "#CC79A7", "#56B4E9", "#E6C800", "#6A3D9A", "#1B7837", "#4D4D4D")
DEFECT_RED = "#B91C1C"
SITE_ORANGE = "#C2410C"
DARK_INK = "#111827"
# Shapes on a 24 x 24 grid, filled with fill-rule evenodd (a shape inside a shape is a hole).
ICONS = {
    "curb-ramp": "M2 20v-3h6l7-5h7v8z M15 15.5a1 1 0 1 0 0.01 0z M18 15.5a1 1 0 1 0 0.01 0z M15 18a1 1 0 1 0 0.01 0z M18 18a1 1 0 1 0 0.01 0z",
    "street-light": "M11 21V6a3 3 0 0 1 3-3h4v2h-4a1 1 0 0 0-1 1v15z M15 5h6l-1 3h-4z M8 20h8v2H8z",
    "tree": "M12 2a6 6 0 0 0-6 6c0 2.6 1.7 4.8 4 5.6V20H8v2h8v-2h-2v-6.4c2.3-.8 4-3 4-5.6a6 6 0 0 0-6-6z",
    "litter-bin": "M9 2h6v2h5v2H4V4h5z M5 8h14l-1.5 14h-11z M9 10v10h1.5V10z M13.5 10v10H15V10z",
    "parking-kiosk": "M6 2h12v14H6z M9 5v8h2v-3h2a2.5 2.5 0 0 0 0-5z M11 7h2a.5.5 0 0 1 0 1h-2z M11 16h2v6h-2z",
    "tiles": "M3 3h5v5H3z M10 3h5v5h-5z M17 3h4v5h-4z M3 10h5v5H3z M17 10h4v5h-4z M3 17h5v4H3z M10 17h5v4h-5z M17 17h4v4h-4z",
    "cover": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 6a6 6 0 1 1 0 12a6 6 0 1 1 0-12z M7 11h10v2H7z",
    "block": "M9 5a3 3 0 0 1 6 0v15H9z M7 20h10v2H7z",
    "cone": "M10 3h4l5 16H5z M3 19h18v2H3z",
    "door": "M6 2h12v20H6z M14.5 11a1.2 1.2 0 1 0 0.01 0z",
}


def is_light(hex_colour: str) -> bool:
    """A fill a white icon would not stand out on (the web app's isLight: relative luminance above 0.35)."""
    n = int(hex_colour[1:], 16)

    def linear(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear((n >> s) & 255) for s in (16, 8, 0))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.35


def item_styles(items: list[dict]) -> dict[str, dict]:
    """{item id: {"color", "icon_color", "icon", "letter"}} for the scan's items in their order. The n-th item takes
    the n-th palette colour, wrapping round; "icon" is an SVG path, or None when the shape is not in ICONS, and then
    the marker shows "letter", the first letter of the item's target."""
    out = {}
    for n, item in enumerate(items):
        color = PALETTE[n % len(PALETTE)]
        out[item["id"]] = {"color": color, "icon_color": DARK_INK if is_light(color) else "#FFFFFF",
                           "icon": ICONS.get(item.get("map_icon") or ""),
                           "letter": ((item.get("target") or item["id"]).strip()[:1] or "?").upper()}
    return out


def marker_svg(style: dict, size: int = 20) -> str:
    """The item's map marker as inline SVG: a circle in its colour with its shape, or its letter."""
    inner = (f'<path d="{style["icon"]}" fill="{style["icon_color"]}" fill-rule="evenodd" '
             'transform="translate(5 5) scale(0.5833)"/>' if style["icon"] else
             f'<text x="12" y="16.5" text-anchor="middle" font-size="12" font-weight="700" '
             f'font-family="system-ui, sans-serif" fill="{style["icon_color"]}">{escape(style["letter"])}</text>')
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" aria-hidden="true">'
            f'<circle cx="12" cy="12" r="11" fill="{style["color"]}" stroke="#FFFFFF" stroke-width="1.5"/>'
            f"{inner}</svg>")
