"""Seoul's street-tree list as the GeoJSON layer eval.city_check reads (eval/cities/seoul.toml):

    python -m eval.seoul_trees TREES.xlsx [--out data/seoul/assets/trees.geojson]

The list is Seoul Open Data Plaza OA-23040, "서울시 가로수 위치 정보" (2026-08-26): an xlsx with one sheet per
district, each a title row, a "관리청" row, then the columns 자치구, 노선, 수종, 도로명 주소, 지번 주소, 좌표(경도),
좌표(위도). It names no tree by a number, so each tree gets an OBJECTID by sheet and row order; a new download may
number them differently. Read with the standard library only.
"""
import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
LNG, LAT = "좌표(경도)", "좌표(위도)"
KOREA = (124.0, 33.0, 132.0, 39.0)  # min lng, min lat, max lng, max lat: a point outside is a typo, not a tree


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _shared(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root.findall(NS + "si")]


def _sheets(z: zipfile.ZipFile) -> list[str]:
    """The worksheets in their number order (sheet2 before sheet10)."""
    names = [n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
    return sorted(names, key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))


def _rows(z: zipfile.ZipFile, sheet: str, shared: list[str]):
    """Each row as {column index: text}."""
    for _, el in ET.iterparse(z.open(sheet)):
        if el.tag != NS + "row":
            continue
        row = {}
        for c in el.findall(NS + "c"):
            v = c.find(NS + "v")
            if c.get("t") == "s" and v is not None:
                text = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(NS + "t"))
            else:
                text = v.text if v is not None else None
            row[_col(c.get("r"))] = text.strip() if isinstance(text, str) else text
        el.clear()
        yield row


def features(path: Path) -> list[dict]:
    """Every tree with a position in Korea as a GeoJSON Point feature: OBJECTID, then the list's own columns."""
    out = []
    with zipfile.ZipFile(path) as z:
        shared = _shared(z)
        for sheet in _sheets(z):
            head = None
            for row in _rows(z, sheet, shared):
                if head is None:
                    if LNG in row.values() and LAT in row.values():
                        head = {i: name for i, name in row.items() if name}
                    continue
                props = {name: row.get(i) for i, name in head.items()}
                try:
                    lng, lat = float(props.pop(LNG)), float(props.pop(LAT))
                except (TypeError, ValueError):
                    continue
                if not (KOREA[0] <= lng <= KOREA[2] and KOREA[1] <= lat <= KOREA[3]):
                    continue
                out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
                            "properties": {"OBJECTID": len(out) + 1, **props}})
    return out


def main(argv: list[str] | None = None) -> Path:
    from pipeline import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--out", type=Path, help="default: <data>/seoul/assets/trees.geojson")
    args = ap.parse_args(argv)
    out = args.out or config.data_dir() / "seoul" / "assets" / "trees.geojson"
    found = features(args.xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": found}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(found)} trees: {out}")
    return out


if __name__ == "__main__":
    main()
