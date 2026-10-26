"""Gate test 4: does SAM 3.1 track tactile paving and obstacles standing on it?

Output contract for the gate report (spike/out/sidewalk.json, keyed by clip file):
- coverage[phrase] / coverage_counts[phrase]: per-phrase share of in-range photos with a confident
  mask; coverage_counts gives {"scored", "failed"} photo counts.
- coverage_any_phrase / coverage_any_counts: the gate's tactile number. A photo in the truth tactile
  ranges is hit if any phrase whose request succeeded on its chunk has a confident detection there,
  failed if no phrase succeeded on its chunk. Use this with its counts.
- events[]: detected is True, False, or None. None (with scored False) means the window overlapped a
  failed request or the label was not queried. Consumers must filter on scored before computing rates.
  overlap_detected is informational (box overlap) and is None when the best-phrase request failed.
- "_meta": {"truth_sha256": ...} for the truth.json the run used. Consumers skip keys starting with "_"
  (truth.clip_entries).
- errors[] also holds requests whose response had a frame index past the chunk's photo count.
- Raw SAM text per request: spike/out/sidewalk/<stem>_<chunk start>_<phrase with underscores>.txt, written
  before parsing, so results can be re-scored offline at other confidence cuts.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from spike import config, video
from spike import meta_client as mc
from spike.console import utf8_stdout
from spike.sam_parse import Detection, boxes_intersect, check_frames, confident, parse_sam_output
from spike.truth import ClipTruth, clip_entries, in_ranges, load_truth, truth_sha256

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
    photos: int = 0  # photos in the uploaded chunk, from probing it


def error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def raw_name(stem: str, start: float, phrase: str) -> str:
    """File name for one request's raw SAM text: <stem>_<chunk start>_<phrase with underscores>.txt."""
    return f"{stem}_{int(start)}_{re.sub(r'[^\w-]+', '_', phrase.strip())}.txt"


def prepare_chunks(path: Path, duration: float, tmp: Path) -> list[Chunk]:
    """Cut the clip into 60-photo chunks and upload each once. A chunk that fails to cut or upload is kept with prep_error."""
    chunks = []
    start = 0.0
    while start < duration - 0.5:
        end = min(start + CHUNK_S, duration)
        file_id, billed, prep_error, photos = None, 0, None, 0
        try:
            chunk = video.cut(path, tmp / f"{path.stem}_{int(start)}_side.mp4", start, end)
            photos = round(video.probe(chunk).duration_s * FPS)
            billed = max(mc.SAM_MIN_BILLED_FRAMES, photos)
            file_id = mc.upload(chunk)
        except Exception as exc:  # one bad chunk must not stop the clip
            prep_error = error_text(exc)
        chunks.append(Chunk(start, end, file_id, billed, prep_error, photos))
        start = end
    return chunks


def run_phrase(chunks: list[Chunk], phrase: str, raw_dir: Path,
               stem: str) -> tuple[list[tuple[float, Detection]], dict[float, str]]:
    """Detections as (photo time, detection), plus {chunk_start: error} for chunks whose request failed.

    Each response is saved to raw_dir before parsing, so it can be re-scored offline (other confidence cuts)
    without paying again. A response with a frame index past the chunk's photo count is that chunk's error.
    """
    hits: list[tuple[float, Detection]] = []
    failed: dict[float, str] = {}
    for c in chunks:
        if c.file_id is None:
            failed[c.start] = c.prep_error or "RuntimeError: chunk was not uploaded"
            continue
        try:
            text = mc.sam(phrase, mc.video_part(c.file_id), billed_frames=c.billed)
            (raw_dir / raw_name(stem, c.start, phrase)).write_text(text, encoding="utf-8")
            parsed = parse_sam_output(text)
            check_frames(parsed, c.photos)
            hits += [(c.start + d.frame / FPS, d) for d in confident(parsed, CONF)]
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


def score_any_phrase(tactile: dict, chunks: list[Chunk], sample_keys: list[int]) -> tuple[float | None, int, int]:
    """Share of in-range photos hit by at least one phrase. A photo is failed if no phrase succeeded on its chunk."""
    hit_keys = {p: {frame_key(t) for t, _ in hits} for p, (hits, _) in tactile.items()}
    scored = failed_n = hit = 0
    for k in sample_keys:
        c = chunk_for(k / FPS, chunks)
        ok = [p for p, (_, failed) in tactile.items() if c is not None and c.start not in failed]
        if not ok:
            failed_n += 1
            continue
        scored += 1
        hit += any(k in hit_keys[p] for p in ok)
    return (hit / scored if scored else None), scored, failed_n


def window_bounds(o) -> tuple[float, float]:
    return o.t_start - EVENT_MARGIN_S, o.t_end + EVENT_MARGIN_S


def chunks_overlapping(chunks: list[Chunk], lo: float, hi: float, starts) -> bool:
    return any(c.start in starts and c.start < hi and c.end > lo for c in chunks)


def longest_run(keys: set[int]) -> int:
    best = run = 0
    prev = None
    for k in sorted(keys):
        run = run + 1 if prev is not None and k == prev + 1 else 1
        best = max(best, run)
        prev = k
    return best


def score_event(o, queried: bool, hits, failed: dict[float, str], chunks: list[Chunk]) -> dict:
    lo, hi = window_bounds(o)
    window = [(t, d) for t, d in hits if lo <= t <= hi]
    if window:
        detected = True
    elif not queried or chunks_overlapping(chunks, lo, hi, failed):
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
    utf8_stdout()
    tmp = config.OUT / "tmp"
    tmp.mkdir(exist_ok=True)
    raw_dir = config.OUT / "sidewalk"
    raw_dir.mkdir(exist_ok=True)
    spark_path = config.OUT / "spark_entrances.json"
    spark = clip_entries(json.loads(spark_path.read_text(encoding="utf-8"))) if spark_path.exists() else {}
    out_path = config.OUT / "sidewalk.json"
    report: dict = {"_meta": {"truth_sha256": truth_sha256()}}
    for clip in load_truth():
        if not clip.tactile_ranges and not clip.obstacles:
            continue
        path = config.CLIPS / clip.file
        labels = obstacle_labels(clip, spark)
        duration = video.probe(path).duration_s
        chunks = prepare_chunks(path, duration, tmp)
        try:
            tactile = {p: run_phrase(chunks, p, raw_dir, path.stem) for p in TACTILE_PHRASES}
            obstacles = {label: run_phrase(chunks, label, raw_dir, path.stem) for label in labels}
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
        any_rate, any_scored, any_failed = score_any_phrase(tactile, chunks, sample_keys)
        scored_phrases = [p for p in coverage if coverage[p] is not None]
        best_tactile = max(scored_phrases, key=lambda p: coverage[p]) if scored_phrases else None

        tactile_by_frame: dict[int, list[Detection]] = {}
        best_failed: dict[float, str] = {}
        if best_tactile:
            best_hits, best_failed = tactile[best_tactile]
            for t, d in best_hits:
                tactile_by_frame.setdefault(frame_key(t), []).append(d)

        events = []
        for o in clip.obstacles:
            queried = o.label in obstacles
            hits, failed = obstacles[o.label] if queried else ([], {})
            scored = score_event(o, queried, hits, failed, chunks)
            overlap: bool | None = any(boxes_intersect(d, td) for t, d in scored["window"]
                                       for td in tactile_by_frame.get(frame_key(t), []))
            if not overlap and best_tactile and chunks_overlapping(chunks, *window_bounds(o), best_failed):
                overlap = None  # best-phrase request failed over this window: unknown, not False
            events.append({"label": o.label, "t_start": o.t_start, "t_end": o.t_end,
                           "on_tactile_truth": o.on_tactile, "detected": scored["detected"],
                           "scored": scored["scored"], "max_consecutive_photos": scored["max_consecutive_photos"],
                           "overlap_detected": overlap})
        report[clip.file] = {"coverage": coverage, "coverage_counts": counts, "coverage_any_phrase": any_rate,
                             "coverage_any_counts": {"scored": any_scored, "failed": any_failed},
                             "best_tactile_phrase": best_tactile, "obstacle_labels_queried": labels,
                             "events": events, "errors": errors}
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        n_scored = sum(e["scored"] for e in events)
        n_detected = sum(e["detected"] is True for e in events)
        print(clip.file, "coverage", {p: (coverage[p], counts[p]) for p in coverage},
              "any-phrase", (any_rate, {"scored": any_scored, "failed": any_failed}),
              f"obstacles detected {n_detected}/{n_scored} scored, {len(events) - n_scored} failed/unqueried,",
              "errors", len(errors))


if __name__ == "__main__":
    main()
