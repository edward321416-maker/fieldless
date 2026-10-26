import re
from dataclasses import dataclass

FRAME_RE = re.compile(r"<(\d+)f>")
BOX_START = "<|box"
BOX_RE = re.compile(r"(\d+)\s*<\|box;([^|>]*)\|>")
SNIP_LEN = 120


@dataclass(frozen=True)
class Detection:
    frame: int
    obj: int
    x1: float
    y1: float
    x2: float
    y2: float
    w: int
    h: int
    c: float | None

    @property
    def area_ratio(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1) / (self.w * self.h)


def _fields(raw: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in raw.split(";") if "=" in part)


def _snip(text: str) -> str:
    return text if len(text) <= SNIP_LEN else text[:SNIP_LEN] + "..."


def _detection(frame: int, box: re.Match[str]) -> Detection:
    record = _snip(box.group(0))
    f = _fields(box.group(2))

    def number(key: str) -> float:
        if key not in f:
            raise ValueError(f"frame {frame}: missing field '{key}' in record {record}")
        try:
            return float(f[key])
        except ValueError:
            raise ValueError(
                f"frame {frame}: non-numeric '{key}={f[key]}' in record {record}"
            ) from None

    x1, y1, x2, y2 = number("x1"), number("y1"), number("x2"), number("y2")
    w, h = number("w"), number("h")
    if not (w > 0 and h > 0):
        raise ValueError(f"frame {frame}: non-positive size w={w:g} h={h:g} in record {record}")
    return Detection(
        frame=frame,
        obj=int(box.group(1)),
        x1=x1, y1=y1, x2=x2, y2=y2,
        w=int(w), h=int(h),
        c=number("c") if "c" in f else None,
    )


def parse_sam_output(text: str) -> list[Detection]:
    detections: list[Detection] = []
    for line in text.splitlines():
        line = line.strip()
        if BOX_START not in line:
            continue
        frame_match = FRAME_RE.match(line)
        if not frame_match:
            raise ValueError(f"unrecognized SAM output: {_snip(line)}")
        frame = int(frame_match.group(1))
        boxes = list(BOX_RE.finditer(line, frame_match.end() - 1))
        if line.count(BOX_START) != len(boxes):
            raise ValueError(f"frame {frame}: malformed SAM record in {_snip(line)}")
        detections.extend(_detection(frame, box) for box in boxes)
    return detections


def confident(dets: list[Detection], threshold: float) -> list[Detection]:
    return [d for d in dets if d.c is None or d.c >= threshold]


def boxes_intersect(a: Detection, b: Detection) -> bool:
    return a.x1 < b.x2 and b.x1 < a.x2 and a.y1 < b.y2 and b.y1 < a.y2
