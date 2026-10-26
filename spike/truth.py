import json
from dataclasses import dataclass
from pathlib import Path

from spike import config


@dataclass(frozen=True)
class Entrance:
    t: float
    name: str
    step_count: int
    has_ramp: bool
    door_type: str


@dataclass(frozen=True)
class Obstacle:
    t_start: float
    t_end: float
    label: str
    on_tactile: bool


@dataclass(frozen=True)
class ClipTruth:
    file: str
    entrances: list[Entrance]
    tactile_ranges: list[tuple[float, float]]
    obstacles: list[Obstacle]


def load_truth(path: Path | None = None) -> list[ClipTruth]:
    path = path or config.CLIPS / "truth.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        ClipTruth(
            file=file,
            entrances=[Entrance(**e) for e in clip.get("entrances", [])],
            tactile_ranges=[(float(a), float(b)) for a, b in clip.get("tactile_ranges", [])],
            obstacles=[Obstacle(**o) for o in clip.get("obstacles", [])],
        )
        for file, clip in raw["clips"].items()
    ]


def match_times(pred: list[float], truth: list[float], tol: float) -> tuple[int, int, int]:
    unused = sorted(pred)
    hits = 0
    for t in sorted(truth):
        best = None
        for p in unused:
            if abs(p - t) <= tol and (best is None or abs(p - t) < abs(best - t)):
                best = p
        if best is not None:
            unused.remove(best)
            hits += 1
    return hits, len(truth) - hits, len(unused)


def in_ranges(t: float, ranges: list[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in ranges)
