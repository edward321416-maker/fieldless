"""Stages 0b and 1 (spec 7.1): cut each clip into 60-photo chunks, build the videos, and ask Spark which entrances
and which kinds of sidewalk obstacles appear. Prompt and schema are the ones that passed the D1 gate
(spike/run_spark_entrances.py); photo numbers are burned in so Spark reads positions instead of guessing them."""
from dataclasses import dataclass
from pathlib import Path

from pipeline import media

CHUNK = 60
SPARK_HOLD = 2  # D1: Spark samples an uploaded video once every 2 s

SCAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entrances", "obstacle_types"],
    "properties": {
        "entrances": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["t", "side", "name", "category", "has_visible_steps"],
                "properties": {
                    "t": {"type": "number"},
                    "side": {"type": "string", "enum": ["left", "right", "center"]},
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "has_visible_steps": {"type": "boolean"},
                },
            },
        },
        "obstacle_types": {"type": "array", "items": {"type": "string"}},
    },
}

SCAN_PROMPT = """You are surveying street accessibility from street-level photos taken by a pedestrian, shown as a video in which each photo stays on screen for two seconds.
A timer burned into the top-left corner of every frame shows the photo number.
1) List every shop or building entrance (door) the photos pass. For each, set t to the timer value shown when that entrance is most clearly and frontally visible. Read t from the timer; do not estimate it.
   Also give the side of the frame (left, right, center), the sign name as written (keep the original language), a short English business category, and whether steps in front of the door are visible.
2) List the kinds of objects standing on the sidewalk that could block a pedestrian or tactile paving, as short English noun phrases such as "electric scooter", "bicycle", "sandwich board sign", "stacked boxes". Empty list if none.
Return JSON only."""


@dataclass
class Chunk:
    clip: int
    idxs: list[int]  # global frame idx of each photo, in order; video frame n is idxs[n]
    video: str  # 1 fps, at most 1280 wide: SAM requests
    spark_video: str  # photo numbers burned in, each photo held SPARK_HOLD s: Spark requests
    width: int
    height: int


def make_chunks(frames: list[dict]) -> list[list[dict]]:
    """Frames grouped by clip in idx order, each clip cut into pieces of at most CHUNK photos."""
    by_clip: dict[int, list[dict]] = {}
    for f in sorted(frames, key=lambda f: f["idx"]):
        by_clip.setdefault(f["clip"], []).append(f)
    return [clip[i:i + CHUNK] for clip in by_clip.values() for i in range(0, len(clip), CHUNK)]


def build_chunk(chunk_frames: list[dict], photos: dict[int, Path], workdir: Path) -> Chunk:
    first = chunk_frames[0]["idx"]
    workdir.mkdir(parents=True, exist_ok=True)
    base = workdir / f"chunk_{first:04d}.mp4"
    width, height = media.images_to_mp4([photos[f["idx"]] for f in chunk_frames], base)
    numbered = media.burn_photo_numbers(base, workdir / f"chunk_{first:04d}_numbers.mp4")
    held = media.hold_frames(numbered, workdir / f"chunk_{first:04d}_spark.mp4", SPARK_HOLD)
    numbered.unlink()
    return Chunk(chunk_frames[0]["clip"], [f["idx"] for f in chunk_frames], str(base), str(held), width, height)


def to_candidates(answer: dict, chunk: Chunk) -> dict:
    """Spark's answer with photo numbers turned into frame idx. A number outside the chunk is counted, not kept."""
    entrances, out_of_range = [], 0
    for e in answer["entrances"]:
        t = round(e["t"])
        if not 0 <= t < len(chunk.idxs):
            out_of_range += 1
            continue
        entrances.append({"frame_idx": chunk.idxs[t], "clip": chunk.clip, "side": e["side"], "name": e["name"].strip(),
                          "category": e["category"].strip(), "has_visible_steps": e["has_visible_steps"]})
    types = sorted({o.strip().lower() for o in answer["obstacle_types"] if o.strip()})
    return {"entrances": entrances, "obstacle_types": types, "out_of_range": out_of_range}


def scan_chunk(api, chunk: Chunk) -> dict:
    with api.uploaded(Path(chunk.spark_video)) as file_id:
        answer = api.spark_video_json(SCAN_PROMPT, file_id, "street_scan", SCAN_SCHEMA)
    return to_candidates(answer, chunk)
