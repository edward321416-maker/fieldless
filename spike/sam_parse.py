import re
from dataclasses import dataclass

FRAME_RE = re.compile(r"<(\d+)f>")
BOX_RE = re.compile(r"(\d+)\s*<\|box;([^|>]*)\|>")


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


def parse_sam_output(text: str) -> list[Detection]:
    detections: list[Detection] = []
    for line in text.splitlines():
        line = line.strip()
        frame_match = FRAME_RE.match(line)
        if not frame_match:
            continue
        frame = int(frame_match.group(1))
        for box in BOX_RE.finditer(line, frame_match.end() - 1):
            f = _fields(box.group(2))
            detections.append(Detection(
                frame=frame,
                obj=int(box.group(1)),
                x1=float(f["x1"]), y1=float(f["y1"]), x2=float(f["x2"]), y2=float(f["y2"]),
                w=int(float(f["w"])), h=int(float(f["h"])),
                c=float(f["c"]) if "c" in f else None,
            ))
    return detections


def confident(dets: list[Detection], threshold: float) -> list[Detection]:
    return [d for d in dets if d.c is None or d.c >= threshold]


def boxes_intersect(a: Detection, b: Detection) -> bool:
    return a.x1 < b.x2 and b.x1 < a.x2 and a.y1 < b.y2 and b.y1 < a.y2
