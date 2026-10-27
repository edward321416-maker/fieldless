"""Survey standards written as sentences (design docs/specs/2026-10-28-us-survey-items-design.md).

What to survey lives in one TOML file per standard in pipeline/standards/, not in code. A [[check]] names what SAM
3.1 should find in the photos (`find`), the noun the survey is about (`target`, `find` by default) and the question
Muse Spark answers about it (`ask`); `report_when` is the answer that makes it a defect for a person to confirm.
An `inventory = true` check maps assets instead: every confirmed object is a point, with no defect answer.
[path] names the walking surface and the objects that may block it. KR keeps the tactile defect stage validated on Seongsu (pipeline/defects.py); its file only switches it on.
"""
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "standards"
ANSWERS = ("yes", "no")
KR_DURABLE = ("missing_blocks", "cover_cut", "fixed_obstruction")  # spec 9.3: the KR defect classes
RESERVED = (*KR_DURABLE, "obstruction")  # issue types other stages write
CHECK_ID = re.compile(r"[a-z][a-z0-9_]*")
KOREA = (124.0, 33.0, 132.0, 39.0)  # min_lng, min_lat, max_lng, max_lat


@dataclass(frozen=True)
class PathSpec:
    surface: str  # SAM 3.1 phrase for the walking surface
    name: str  # the surface in a question: "Is {name} actually blocked ...?"
    describe: str  # what the survey is about: "a survey of {describe}"
    objects: tuple[str, ...]  # always asked for, besides the kinds Spark lists
    fixed: tuple[str, ...]  # fixed objects always asked for; empty: a fixed object on the path is not reported


@dataclass(frozen=True)
class Check:
    id: str
    find: str
    target: str  # the noun the survey is about; SAM's phrase `find` may differ
    ask: str
    report_when: str
    label_yes: str
    label_no: str
    label_unclear: str
    icon_yes: str = ""
    icon_no: str = ""
    inventory: bool = False  # every confirmed object is an asset point; there is no defect answer

    def label(self, answer: str | None) -> tuple[str, str]:
        """(icon, text) for an answer; anything but yes or no is unclear."""
        if answer == "yes":
            return self.icon_yes, self.label_yes
        if answer == "no":
            return self.icon_no, self.label_no
        return "?", self.label_unclear


@dataclass(frozen=True)
class Standard:
    code: str
    path: PathSpec
    checks: tuple[Check, ...]
    tactile_defects: bool

    def check(self, check_id: str) -> Check | None:
        return next((c for c in self.checks if c.id == check_id), None)

    def is_defect(self, row: dict) -> bool:
        """A KR defect class, or a check row whose current answer is the check's report_when (never an inventory row)."""
        check = self.check(row["issue_type"])
        if check is not None:
            return not check.inventory and row.get("object_label") == check.report_when
        return row["issue_type"] in KR_DURABLE


def _need(table: dict, key: str, where: str):
    if key not in table:
        raise ValueError(f"{where}: missing '{key}'")
    return table[key]


def _check(table: dict, where: str) -> Check:
    check_id = _need(table, "id", where)
    if not CHECK_ID.fullmatch(check_id):
        raise ValueError(f"{where}: id {check_id!r} must be lower case letters, digits and _")
    if check_id in RESERVED:
        raise ValueError(f"{where}: id {check_id!r} is reserved for another stage")
    inventory = bool(table.get("inventory", False))
    find = _need(table, "find", where)
    target = table.get("target") or find
    if inventory:
        if "report_when" in table:
            raise ValueError(f"{where}: an inventory check has no report_when (every confirmed {target} is an asset)")
        report = ""
        label_yes = table.get("label_yes") or target[:1].upper() + target[1:]
        label_no = table.get("label_no") or f"Not a {target}"
        icon_yes, icon_no = table.get("icon_yes") or "●", table.get("icon_no") or "–"
    else:
        report = _need(table, "report_when", where)
        if report not in ANSWERS:
            raise ValueError(f"{where}: report_when must be 'yes' or 'no', not {report!r}")
        label_yes, label_no = _need(table, "label_yes", where), _need(table, "label_no", where)
        defect_icon, ok_icon = "✖", "✓"
        icon_yes = table.get("icon_yes") or (defect_icon if report == "yes" else ok_icon)
        icon_no = table.get("icon_no") or (defect_icon if report == "no" else ok_icon)
    unclear = table.get("label_unclear") or f"{target[:1].upper()}{target[1:]}: unclear from the photo"
    return Check(id=check_id, find=find, target=target, ask=_need(table, "ask", where), report_when=report,
                 label_yes=label_yes, label_no=label_no, label_unclear=unclear, icon_yes=icon_yes, icon_no=icon_no,
                 inventory=inventory)


def parse(text: str, where: str) -> Standard:
    """A standard from TOML text; `where` names the file in error messages."""
    data = tomllib.loads(text)
    path = _need(data, "path", where)
    at = f"{where} [path]"
    checks = tuple(_check(c, f"{where} check {i + 1}") for i, c in enumerate(data.get("check", [])))
    ids = [c.id for c in checks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{where}: check ids repeat")
    return Standard(code=_need(data, "standard", where),
                    path=PathSpec(surface=_need(path, "surface", at), name=_need(path, "name", at),
                                  describe=_need(path, "describe", at), objects=tuple(path.get("objects", [])),
                                  fixed=tuple(path.get("fixed", []))),
                    checks=checks, tactile_defects=bool(data.get("tactile_defects", {}).get("enabled", False)))


def load(code: str) -> Standard:
    """The standard in pipeline/standards/<code>.toml (the code is case-insensitive). Re-read when the file changes,
    so a running server sees an edited standard."""
    file = FOLDER / f"{code.lower()}.toml"
    if not file.is_file():
        raise ValueError(f"no survey standard {code!r} (looked for {file.name})")
    return _load(code.upper(), file, file.stat().st_mtime_ns)


@lru_cache(maxsize=16)
def _load(code: str, file: Path, mtime_ns: int) -> Standard:
    std = parse(file.read_text(encoding="utf-8"), file.name)
    if std.code != code:
        raise ValueError(f"{file.name}: standard is {std.code!r}, expected {code!r}")
    return std


def standard_for(bbox) -> str:
    """KR when the area's centre is in Korea, else US."""
    lng, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    min_lng, min_lat, max_lng, max_lat = KOREA
    return "KR" if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat else "US"
