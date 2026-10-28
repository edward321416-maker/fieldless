"""Sidewalk flow, detection (spec 7.3 S2, S3). The walking surface and the objects to look for come from the scan's
survey standard (KR: tactile paving). D1: tactile paving needs full-resolution stills (0.865 of tactile
photos found, 2 of 40 negatives fired; the 1280-wide video found 0.65). Obstacles are tracked on the chunk video
(15 of 17 events found). Tactile paving runs continuously along a street, so every second photo is enough."""
from pathlib import Path

from pipeline import events
from pipeline.cost import CostCapReached
from pipeline.parallel import run_parallel
from pipeline.sam_output import Seg, check_frames, confident, parse
from pipeline.scene import Chunk
from pipeline.survey_items import PathSpec

TACTILE_EVERY = 2
MAX_OBSTACLE_LABELS = 8


def sampled(clip_idxs: list[int], every: int) -> list[int]:
    return clip_idxs[::every]


def surface_segs(api, phrase: str, idxs: list[int], photos: dict[int, Path],
                 workers: int) -> dict[int, list[Seg] | None]:
    """Confident segments of the walking surface (the standard's path.surface) per photo, in photo pixels. None
    marks a photo whose request failed."""
    def one(idx):
        try:
            return confident(parse(api.sam_image(phrase, photos[idx]), video=False))
        except CostCapReached:
            raise
        except Exception:
            return None  # unavailable (spec 14); request failures are in calls.jsonl, unusable answers are not

    return dict(zip(idxs, run_parallel(one, idxs, workers)))


def obstacle_labels(spark_types: list[str], path: PathSpec) -> list[str]:
    """SAM concepts to ask for: the standard's objects and fixed objects, then the kinds Spark listed, at most
    MAX_OBSTACLE_LABELS."""
    spark = [t.strip().lower() for t in spark_types if t.strip()]
    if not path.fixed:
        spark = [t for t in spark if not events.is_fixed(t)]  # US: fixed objects are not reported, so not asked for
    labels = list(path.objects) + list(path.fixed) + spark
    return list(dict.fromkeys(labels))[:MAX_OBSTACLE_LABELS]


def obstacle_segs(api, chunk: Chunk, labels: list[str], workers: int = 1) -> dict[str, dict[int, list[Seg]] | None]:
    """Per label, confident segments by frame idx in video pixels; None when that label's request failed. The
    labels are asked at once (up to workers) on one upload of the chunk."""
    def one(file_id, label):
        try:
            segs = confident(parse(api.sam_video(label, file_id, frames=len(chunk.idxs)), video=True))
            check_frames(segs, len(chunk.idxs))
        except CostCapReached:
            raise
        except Exception:
            return None
        by_idx: dict[int, list[Seg]] = {}
        for s in segs:
            by_idx.setdefault(chunk.idxs[s.frame], []).append(s)
        return by_idx

    try:
        with api.uploaded(Path(chunk.video)) as file_id:
            out = dict(zip(labels, run_parallel(lambda label: one(file_id, label), labels, workers)))
    except CostCapReached:
        raise
    except Exception:
        return {label: None for label in labels}  # the upload failed: this chunk is unavailable (spec 14)
    return out
