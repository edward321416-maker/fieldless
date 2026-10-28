import re
from pathlib import Path

from assist import style
from pipeline import survey_items
from server import views

WEB = Path(__file__).resolve().parents[2] / "web" / "src"


def items(*icons):
    return [{"id": f"i{n}", "target": f"thing {n}", "map_icon": icon} for n, icon in enumerate(icons)]


def test_palette_and_icons_are_the_web_apps_own():
    # The report must look like the app: same colours in the same order, same shapes (web/src/items.ts, icons.ts).
    palette = re.search(r"PALETTE = \[([^\]]*)\]", (WEB / "items.ts").read_text(encoding="utf-8")).group(1)
    assert tuple(re.findall(r'"(#[0-9A-Fa-f]{6})"', palette)) == style.PALETTE
    icons_ts = (WEB / "icons.ts").read_text(encoding="utf-8")
    web_icons = dict(re.findall(r'^\s*"?([a-z][a-z0-9-]*)"?: "([^"]+)",?$', icons_ts, re.M))
    assert web_icons == style.ICONS


def test_the_nth_item_takes_the_nth_palette_colour_and_the_palette_wraps():
    got = style.item_styles(items(*["tree"] * (len(style.PALETTE) + 1)))
    assert [got[f"i{n}"]["color"] for n in range(len(style.PALETTE))] == list(style.PALETTE)
    assert got[f"i{len(style.PALETTE)}"]["color"] == style.PALETTE[0]


def test_a_light_colour_takes_a_dark_icon_and_no_colour_is_red_or_orange():
    got = style.item_styles(items(*["tree"] * len(style.PALETTE)))
    for s in got.values():
        assert s["icon_color"] == ("#111827" if style.is_light(s["color"]) else "#FFFFFF")
    assert style.is_light("#E6C800") and not style.is_light("#0072B2")
    assert style.DEFECT_RED not in style.PALETTE and style.SITE_ORANGE not in style.PALETTE


def test_an_item_without_a_known_icon_shows_the_first_letter_of_its_target():
    got = style.item_styles([{"id": "x", "target": "bench", "map_icon": ""},
                             {"id": "y", "target": "kiosk", "map_icon": "no-such-shape"}])
    assert got["x"]["icon"] is None and got["x"]["letter"] == "B"
    assert got["y"]["icon"] is None and got["y"]["letter"] == "K"


def test_stage_items_use_shapes_from_the_icon_set():
    assert {v["map_icon"] for v in views.STAGE_ITEMS.values()} <= set(style.ICONS)


def test_marker_svg_draws_the_colour_and_the_icon_or_the_letter():
    s = style.item_styles(items("tree", ""))
    svg = style.marker_svg(s["i0"], size=24)
    assert svg.startswith("<svg") and s["i0"]["color"] in svg and style.ICONS["tree"] in svg
    assert 'aria-hidden="true"' in svg and 'width="24"' in svg and 'fill-rule="evenodd"' in svg
    assert ">T<" in style.marker_svg(s["i1"])
