"""Gate test 4: does SAM 3.1 track tactile paving and obstacles standing on it?"""
import json
from dataclasses import dataclass
from pathlib import Path

from spike import config, video
from spike import meta_client as mc
from spike.sam_parse import Detection, boxes_intersect, confident, parse_sam_output
from spike.truth import ClipTruth, in_ranges, load_truth

TACTILE_PHRASES = ["tactile paving", "yellow tactile paving", "braille block"]
BASE_OBSTACLES = ["electric scooter", "bicycle"]
MAX_OBSTACLE_LABELS = 6
FPS = config.CLIP_FPS  # every photo is one frame
CHUNK_S = 60.0
CONF = 0.5
EVENT_MARGIN_S = 1.0  # ±1 photo


@dataclass(frozen=True)
class Chunk:
    start: float
    end: float
    file_id: str | None
    billed: int
    prep_error: str | None = None


def error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def prepare_chunks(path: Path, duration: float, tmp: Path) -> list[Chunk]:
    """Cut the clip into 60-photo chunks and upload each once. A chunk that fails to cut or upload is kept with prep_error."""
    chunks = []
    start = 0.0
    while start < duration - 0.5:
        end = min(start + CHUNK_S, duration)
        file_id, billed, prep_error = None, 0, None
        try:
            chunk = video.cut(path, tmp / f"{path.stem}_{int(start)}_side.mp4", start, end)
            billed = max(mc.SAM_MIN_BILLED_FRAMES, round(video.probe(chunk).duration_s * FPS))
            file_id = mc.upload(chunk)
        except Exception as exc:  # one bad chunk must not stop the clip
            prep_error = error_text(exc)
        chunks.append(Chunk(start, end, file_id, billed, prep_error))
        start = end
    return chunks


def run_phrase(chunks: list[Chunk], phrase: str) -> tuple[list[tuple[float, Detection]], dict[float, str]]:
    """Detections as (photo time, detection), plus {chunk_start: error} for chunks whose request failed."""
    hits: list[tuple[float, Detection]] = []
    failed: dict[float, str] = {}
    for c in chunks:
        if c.file_id is None:
            failed[c.start] = c.prep_error or "RuntimeError: chunk was not uploaded"
            continue
        try:
            text = mc.sam(phrase, mc.video_part(c.file_id), billed_frames=c.billed)
            hits += [(c.start + d.frame / FPS, d) for d in confident(parse_sam_output(text), CONF)]
        except Exception as exc:  # a failed request is recorded, never counted as "no detection"
            failed[c.start] = error_text(exc)
    return hits, failed


def frame_key(t: float) -> int:
    return round(t * FPS)


def chunk_for(t: float, chunks: list[Chunk]) -> Chunk | None:
    return next((c for c in chunks if c.start <= t < c.end), None)


def score_coverage(hits, failed: dict[float, str], chunks: list[Chunk],
                   sample_keys: list[int]) -> tuple[float | None, int, int]:
    """Share of scored photos with a hit. Photos in failed chunks are excluded and counted as failed."""
    hit_keys = {frame_key(t) for t, _ in hits}
    scored = failed_n = hit = 0
    for k in sample_keys:
        c = chunk_for(k / FPS, chunks)
        if c is None or c.start in failed:
            failed_n += 1
            continue
        scored += 1
        hit += k in hit_keys
    return (hit / scored if scored else None), scored, failed_n


def longest_run(keys: set[int]) -> int:
    best = run = 0
    prev = None
    for k in sorted(keys):
        run = run + 1 if prev is not None and k == prev + 1 else 1
        best = max(best, run)
        prev = k
    return best


def score_event(o, queried: bool, hits, failed: dict[float, str], chunks: list[Chunk]) -> dict:
    lo, hi = o.t_start - EVENT_MARGIN_S, o.t_end + EVENT_MARGIN_S
    window = [(t, d) for t, d in hits if lo <= t <= hi]
    if window:
        detected = True
    elif not queried or any(c.start in failed and c.start < hi and c.end > lo for c in chunks):
        detected = None  # not queried or a request covering the window failed: unscored, not a miss
    else:
        detected = False
    return {"detected": detected, "scored": detected is not None, "window": window,
            "max_consecutive_photos": longest_run({frame_key(t) for t, _ in window})}


def obstacle_labels(clip: ClipTruth, spark: dict) -> list[str]:
    # Truth labels are never dropped by the cap, so every labeled event gets queried.
    truth = list(dict.fromkeys(o.label for o in clip.obstacles))
    extra = [label for label in dict.fromkeys(BASE_OBSTACLES + spark.get(clip.file, {}).get("obstacle_types", []))
             if label not in truth]
    return truth + extra[:max(0, MAX_OBSTACLE_LABELS - len(truth))]


def main() -> None:
    tmp = config.OUT / "tmp"
    tmp.mkdir(exist_ok=True)
    spark_path = config.OUT / "spark_entrances.json"
    spark = json.loads(spark_path.read_text(encoding="utf-8")) if spark_path.exists() else {}
    out_path = config.OUT / "sidewalk.json"
    report = {}
    for clip in load_truth():
        if not clip.tactile_ranges and not clip.obstacles:
            continue
        path = config.CLIPS / clip.file
        labels = obstacle_labels(clip, spark)
        duration = video.probe(path).duration_s
        chunks = prepare_chunks(path, duration, tmp)
        try:
            tactile = {p: run_phrase(chunks, p) for p in TACTILE_PHRASES}
            obstacles = {label: run_phrase(chunks, label) for label in labels}
        finally:
            for c in chunks:
                if c.file_id:
                    mc.delete_file(c.file_id)

        errors = []
        for stage, results in (("tactile", tactile), ("obstacle", obstacles)):
            for item, (_, failed) in results.items():
                errors += [{"stage": stage, "item": item, "chunk_start": s, "error": e} for s, e in failed.items()]

        sample_keys = [k for k in range(round(duration * FPS)) if in_ranges(k / FPS, clip.tactile_ranges)]
        coverage: dict[str, float | None] = {}
        counts: dict[str, dict[str, int]] = {}
        for phrase, (hits, failed) in tactile.items():
            rate, scored, failed_n = score_coverage(hits, failed, chunks, sample_keys)
            coverage[phrase] = rate
            counts[phrase] = {"scored": scored, "failed": failed_n}
        scored_phrases = [p for p in coverage if coverage[p] is not None]
        best_tactile = max(scored_phrases, key=lambda p: coverage[p]) if scored_phrases else None

        tactile_by_frame: dict[int, list[Detection]] = {}
        for t, d in tactile[best_tactile][0] if best_tactile else []:
            tactile_by_frame.setdefault(frame_key(t), []).append(d)

        events = []
        for o in clip.obstacles:
            queried = o.label in obstacles
            hits, failed = obstacles[o.label] if queried else ([], {})
            scored = score_event(o, queried, hits, failed, chunks)
            overlap = any(boxes_intersect(d, td) for t, d in scored["window"]
                          for td in tactile_by_frame.get(frame_key(t), []))
            events.append({"label": o.label, "t_start": o.t_start, "t_end": o.t_end,
                           "on_tactile_truth": o.on_tactile, "detected": scored["detected"],
                           "scored": scored["scored"], "max_consecutive_photos": scored["max_consecutive_photos"],
                           "overlap_detected": overlap})
        report[clip.file] = {"coverage": coverage, "coverage_counts": counts, "best_tactile_phrase": best_tactile,
                             "obstacle_labels_queried": labels, "events": events, "errors": errors}
        (config.OUT / "sidewalk.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        n_scored = sum(e["scored"] for e in events)
        n_detected = sum(e["detected"] is True for e in events)
        print(clip.file, "coverage", {p: (coverage[p], counts[p]) for p in coverage},
              f"obstacles detected {n_detected}/{n_scored} scored, {len(events) - n_scored} failed/unqueried,",
              "errors", len(errors))


if __name__ == "__main__":
    main()
