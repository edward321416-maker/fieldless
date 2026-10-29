import json
import zipfile
from xml.sax.saxutils import escape

from eval import city_check, seoul_trees

HEAD = ["자치구", "노선", "수종", "도로명 주소", "지번 주소", "좌표(경도)", "좌표(위도)"]


def _sheet(rows: list[list], shared: list[str]) -> str:
    """A worksheet: text in shared strings, numbers as plain values, as the city's file has both."""
    out = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value is None:
                continue
            if isinstance(value, float):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                shared.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(out) + "</sheetData></worksheet>")


def _xlsx(path, sheets: dict[str, list[list]]):
    shared: list[str] = []
    files = {name: _sheet(rows, shared) for name, rows in sheets.items()}
    with zipfile.ZipFile(path, "w") as z:
        for name, xml in files.items():
            z.writestr(f"xl/worksheets/{name}.xml", xml)
        z.writestr("xl/sharedStrings.xml",
                   '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")


def test_each_tree_with_a_position_becomes_a_point_numbered_by_sheet_and_row(tmp_path):
    path = tmp_path / "trees.xlsx"
    _xlsx(path, {
        "sheet10": [["가로수 위치 정보"], ["관리청: 성동구"], HEAD,
                    ["성동구", "성수이로", "은행나무", "서울특별시 성동구 성수이로 1", "서울특별시 성동구 성수동2가 1", "127.0561", "37.5480"]],
        "sheet2": [["가로수 위치 정보"], ["관리청: 종로구"], HEAD,
                   ["종로구", "경교장길", "양버즘나무", "서울특별시 종로구 경교장길 8", "서울특별시 종로구 평동 226", 126.966028, 37.56761832],
                   ["종로구", "경교장길", "은행나무", None, None, None, None],
                   ["종로구", "경교장길", "은행나무", None, None, "0", "0"]],
    })

    found = seoul_trees.features(path)

    assert [f["properties"]["OBJECTID"] for f in found] == [1, 2]
    assert found[0] == {"type": "Feature", "geometry": {"type": "Point", "coordinates": [126.966028, 37.56761832]},
                        "properties": {"OBJECTID": 1, "자치구": "종로구", "노선": "경교장길", "수종": "양버즘나무",
                                       "도로명 주소": "서울특별시 종로구 경교장길 8",
                                       "지번 주소": "서울특별시 종로구 평동 226"}}
    assert found[1]["properties"]["자치구"] == "성동구" and found[1]["geometry"]["coordinates"] == [127.0561, 37.548]


def test_cli_writes_the_layer_the_seoul_file_names(tmp_path, capsys):
    path, out = tmp_path / "trees.xlsx", tmp_path / "seoul" / "assets" / "trees.geojson"
    _xlsx(path, {"sheet1": [HEAD, ["성동구", "성수이로", "은행나무", "a", "b", "127.0561", "37.548"]]})

    seoul_trees.main([str(path), "--out", str(out)])

    assert len(json.loads(out.read_text(encoding="utf-8"))["features"]) == 1
    assert "1 trees" in capsys.readouterr().out
    town = city_check.load_city("seoul")
    assert town["root"] == "seoul" and town["items"]["street_tree"]["layer"] == "assets/trees.geojson"
    assert town["items"]["street_tree"]["id_fields"] == ["자치구", "노선", "수종", "지번 주소"]
