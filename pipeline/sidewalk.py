"""Sidewalk flow, detection (spec 7.3 S2, S3). D1: tactile paving needs full-resolution stills (0.865 of tactile
photos found, 2 of 40 negatives fired; the 1280-wide video found 0.65). Obstacles are tracked on the chunk video
(15 of 17 events found). Tactile paving runs continuously along a street, so every second photo is enough."""
from pathlib import Path

from pipeline.cost import CostCapReached
from pipeline.parallel import run_parallel
from pipeline.sam_output import Seg, check_frames, confident, parse
from pipeline.scene import Chunk

TACTILE_PHRASE = "yellow tactile paving"  # D1 re-test; "braille block" found nothing
TACTILE_EVERY = 2
BASE_OBSTACLES = ["electric scooter", "bicycle"]
MAX_OBSTACLE_LABELS = 6


def sampled(clip_idxs: list[int], every: int) -> list[int]:
    return clip_idxs[::every]


def tactile_segs(api, idxs: list[int], photos: dict[int, Path], workers: int) -> dict[int, list[Seg] | None]:
    """Confident tactile-paving segments per photo (photo pixels). None marks a photo whose request failed."""
    def one(idx):
        try:
            return confident(parse(api.sam_image(TACTILE_PHRASE, photos[idx]), video=False))
        except CostCapReached:
            raise
        except Exception:
            return None  # unavailable (spec 14); the failure is in calls.jsonl

    return dict(zip(idxs, run_parallel(one, idxs, workers)))


def obstacle_labels(spark_types: list[str]) -> list[str]:
    """SAM concepts to ask for: the base labels, then the kinds Spark listed, at most MAX_OBSTACLE_LABELS."""
    labels = BASE_OBSTACLES + [t.strip().lower() for t in spark_types if t.strip()]
    return list(dict.fromkeys(labels))[:MAX_OBSTACLE_LABELS]


def obstacle_segs(api, chunk: Chunk, labels: list[str]) -> dict[str, dict[int, list[Seg]] | None]:
    """Per label, confident segments by frame idx in video pixels; None when that label's request failed."""
    out: dict[str, dict[int, list[Seg]] | None] = {}
    with api.uploaded(Path(chunk.video)) as file_id:
        for label in labels:
            try:
                segs = confident(parse(api.sam_video(label, file_id, frames=len(chunk.idxs)), video=True))
                check_frames(segs, len(chunk.idxs))
            except CostCapReached:
                raise
            except Exception:
                out[label] = None
                continue
            by_idx: dict[int, list[Seg]] = {}
            for s in segs:
                by_idx.setdefault(chunk.idxs[s.frame], []).append(s)
            out[label] = by_idx
    return out
