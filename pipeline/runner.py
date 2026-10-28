"""One scan through every stage (spec 7): collect, chunks, scene, sidewalk, defects, sidewalk_evidence, entrances,
evidence.

The sidewalk layer is the main layer (spec revision 4), so its stages, including its evidence photos, run before the
entrances. Each stage's status is kept in scan.stage_status, so a scan that failed or hit its cost cap resumes at the
stage that stopped (spec 14). Every stage after scene is independent: one failing does not stop the others. Worker
threads only make API requests; rows are written from the main thread.
"""
import json
import multiprocessing
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline import (collect, config, db, defects, entrance_detect, entrance_read, events, evidence, geo, media,
                      scene, second_opinion, sidewalk, survey_checks, survey_items)
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import RunAborted, error_text
from pipeline.parallel import run_parallel

STAGES = ("collect", "chunks", "scene", "sidewalk", "defects", "sidewalk_evidence", "entrances", "evidence")
INDEPENDENT = {"sidewalk", "defects", "sidewalk_evidence", "entrances", "evidence"}
RERUN_AFTER = {"defects": {"sidewalk"}, "sidewalk_evidence": {"sidewalk", "defects"},
               "evidence": set(STAGES)}  # a done stage reruns if one of these ran
SCENE_WORKERS = 6  # Spark video requests are large; a 400-photo scan has 7 chunks
CHECK_VIDEO_SIDE = 2048  # checks found by video see the photos at their own size (collect keeps 2048-px photos)
RESUME_MIN_USD = 0.10
_photo_locks: dict[str, threading.Lock] = {}
_photo_locks_guard = threading.Lock()


def _original_photo(image_id: str) -> Path:
    """entrance_read.original_photo, one download per image at a time: entrance and evidence workers share the photo
    cache, and a worker must never read a photo another worker is still writing."""
    with _photo_locks_guard:
        lock = _photo_locks.setdefault(image_id, threading.Lock())
    with lock:
        return entrance_read.original_photo(image_id)


class NoPhotos(RuntimeError):
    """The area has no usable recent pedestrian photos (spec 14: show "not enough photos")."""


@dataclass
class Scan:
    conn: object
    scan_id: int
    api: object
    run_dir: Path
    bbox: list[float]
    max_photos: int = config.MAX_PHOTOS_PER_SCAN
    tactile_every: int = sidewalk.TACTILE_EVERY
    workers: int = config.PARALLEL_REQUESTS
    standard: "survey_items.Standard | None" = None  # run_scan fills it from scan.jurisdiction
    second_opinion: object = None  # an OpenModel when BASETEN_API_KEY is set (CLI, server)
    judge: object = None  # Muse Glimmer (an OpenModel) for checks with judge = "glimmer", when GLIMMER_URL is set
    only_checks: tuple[str, ...] = ()  # with --rerun defects --check ID: run and clear only these checks
    frames: list[dict] = field(default_factory=list)
    chunks: list[scene.Chunk] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.max_photos = config.photo_budget(self.max_photos)  # spec 13: 400 at most, and what the cap pays for

    @property
    def work(self) -> Path:
        path = Path(self.run_dir) / "work" / f"scan{self.scan_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state(self, name: str) -> Path:
        return self.work / f"{name}.json"

    def by_idx(self) -> dict[int, dict]:
        return {f["idx"]: f for f in self.frames}

    def photos(self) -> dict[int, Path]:
        return {f["idx"]: collect.photo_path(f["image_id"]) for f in self.frames}

    def clips(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for f in sorted(self.frames, key=lambda f: f["idx"]):
            out.setdefault(f["clip"], []).append(f["idx"])
        return out


class InWorkerProcess(RuntimeError):
    """A scan was about to start inside a worker process."""


def refuse_in_worker() -> None:
    """Raise InWorkerProcess inside a multiprocessing worker. On Windows a worker re-runs the script that started
    the parent; on 2026-10-29 a script without a __main__ guard started a scan in each of 8 mask-decoding workers,
    which spent $23.84 in ten minutes before they were stopped. A scan starts only in a main process."""
    if multiprocessing.parent_process() is not None:
        raise InWorkerProcess("a scan never starts inside a worker process (a script without a __main__ guard?)")


def meter_for(conn, scan: dict, scan_cap_usd: float | None = None, public_cap_usd: float | None = None,
              daily_cap_usd: float | None = None) -> CostMeter:
    """The scan's cap: SCAN_COST_CAP_USD (or scan_cap_usd, for a local CLI run the user approved), and no more than
    what other scans left of today's DAILY_COST_CAP_USD (or daily_cap_usd, likewise approved for one local run). For
    a scan a visitor started, also no more than other visitor scans left of public_cap_usd. Spend from earlier
    attempts of this scan already counts."""
    today = db.now()[:10]
    others = db.daily_cost(conn, today) - (scan["cost_usd"] if scan["created_at"].startswith(today) else 0.0)
    cap = min(scan_cap_usd or config.SCAN_COST_CAP_USD, (daily_cap_usd or config.DAILY_COST_CAP_USD) - others)
    if public_cap_usd is not None and scan.get("client_ip_hash"):
        cap = min(cap, public_cap_usd - (db.public_cost(conn) - scan["cost_usd"]))
    return CostMeter(cap_usd=max(0.0, cap), spent_usd=scan["cost_usd"])


def stage_collect(s: Scan) -> None:
    budget = config.photo_budget(s.max_photos, checks=len(s.standard.checks), cap_usd=s.api.meter.cap_usd)
    frames = collect.collect(tuple(s.bbox), budget)
    if not frames:
        raise NoPhotos("no recent pedestrian photos in this area")
    db.clear_rows(s.conn, s.scan_id, "frame")  # a resumed collect starts clean
    for old in s.work.glob("find_*.json"):
        old.unlink()  # SAM areas saved for the earlier photos
    db.add_frames(s.conn, s.scan_id, frames)
    times = sorted(f["captured_at"] for f in frames)
    db.update_scan(s.conn, s.scan_id, image_count=len(frames), captured_from=times[0], captured_to=times[-1],
                   sequence_ids=sorted({f["sequence_id"] for f in frames}))
    s.frames = frames


def stage_chunks(s: Scan) -> None:
    photos = s.photos()
    s.chunks = [scene.build_chunk(c, photos, s.work) for c in scene.make_chunks(s.frames)]
    s.state("chunks").write_text(json.dumps([asdict(c) for c in s.chunks]), encoding="utf-8")


def _scan_one(s: Scan, chunk: scene.Chunk) -> dict:
    try:
        return scene.scan_chunk(s.api, chunk)
    except CostCapReached:
        raise
    except Exception as exc:  # one chunk failing loses only its entrances
        return {"entrances": [], "obstacle_types": [], "out_of_range": 0, "error": error_text(exc)}


def stage_scene(s: Scan) -> None:
    s.scenes = run_parallel(lambda c: _scan_one(s, c), s.chunks, min(SCENE_WORKERS, s.workers))
    s.state("scene").write_text(json.dumps(s.scenes, ensure_ascii=False), encoding="utf-8")


def _entrance_row(s: Scan, cand: entrance_detect.Candidate, clip_idxs: list[int], by_idx: dict, photos: dict,
                  workdir: Path) -> dict:
    frame = by_idx[cand.frame_idx]
    base = {"frame_idx": cand.frame_idx, "place_name": cand.name, "category": cand.category, "side": cand.side}
    try:
        idxs = entrance_detect.window_idxs(cand.frame_idx, clip_idxs)
        sizes = {i: (by_idx[i]["width"], by_idx[i]["height"]) for i in idxs}
        stairs = entrance_detect.detect_stairs(s.api, idxs, photos, sizes, workdir)
        rep = by_idx[stairs.best_idx if stairs.found else cand.frame_idx]
        photo = _original_photo(rep["image_id"])
        reading = entrance_read.read_entrance(s.api, photo)
        sill = None if stairs.found else entrance_read.outline_sill(s.api, photo, reading)
        measured = entrance_read.measure(stairs.found, reading)
    except CostCapReached:
        raise
    except Exception as exc:  # this entrance only: keep it, flagged for review
        lat, lng, source = geo.entrance_location(frame, cand.side)
        return {**base, "lat": lat, "lng": lng, "location_source": source, "review_status": "needs_review",
                "review_reasons": ["analysis_failed"], "model_outputs": {"error": error_text(exc)},
                "captured_at": frame["captured_at"]}
    lat, lng, source = geo.entrance_location(rep, cand.side)
    return {**base, "frame_idx": rep["idx"], "lat": lat, "lng": lng, "location_source": source,
            "stairs_detected": stairs.found, "sill_possible": "possible_sill" in measured["review_reasons"],
            "confidence": reading["confidence"], **measured,
            "model_outputs": {"stairs": asdict(stairs), "reading": reading, "sill": sill, "merged": cand.merged,
                              "has_visible_steps": cand.has_visible_steps},
            "captured_at": rep["captured_at"]}


def stage_entrances(s: Scan) -> None:
    db.clear_rows(s.conn, s.scan_id, "entrance")
    by_idx, photos, clips = s.by_idx(), s.photos(), s.clips()
    cands = entrance_detect.merge_candidates([e for sc in s.scenes for e in sc["entrances"]], by_idx)
    # each entrance gets its own work folder: two entrances on the same photos must not share a stairs video
    rows = run_parallel(lambda kc: _entrance_row(s, kc[1], clips[kc[1].clip], by_idx, photos,
                                                 s.work / f"entrance{kc[0]}"), list(enumerate(cands)), s.workers)
    for row in rows:
        db.add_entrance(s.conn, s.scan_id, row)


def _issue_row(s: Scan, event: events.Event, by_idx: dict, photos: dict) -> dict | None:
    try:
        check = events.check_event(s.api, photos[event.rep_idx], s.standard.path)
        status, reasons = events.review(check)
    except CostCapReached:
        raise
    except Exception as exc:
        check, status, reasons = {"error": error_text(exc)}, "needs_review", ["check_failed"]
    if status is None:
        return None
    lat, lng = geo.issue_location(by_idx, event.start_idx, event.end_idx)
    issue_type = "fixed_obstruction" if events.is_fixed(event.label) else "obstruction"
    return {"issue_type": issue_type, "object_label": event.label, "frame_start": event.start_idx,
            "frame_end": event.end_idx, "frame_idx": event.rep_idx, "lat": lat, "lng": lng,
            "overlap_ratio": event.max_ratio, "confidence": check.get("confidence"), "review_status": status,
            "review_reasons": reasons, "model_outputs": {"check": check},
            "captured_at": by_idx[event.rep_idx]["captured_at"]}


def stage_sidewalk(s: Scan) -> None:
    db.clear_issues(s.conn, s.scan_id, ("obstruction", "fixed_obstruction"))
    by_idx, photos = s.by_idx(), s.photos()
    unavailable = {"tactile_photos": [], "obstacle_requests": []}
    clips = s.clips()
    # Every walk's requests go out together, so a scan of few walks still keeps s.workers requests going.
    tactile = sidewalk.surface_segs(s.api, s.standard.path.surface,
                                    [i for idxs in clips.values() for i in sidewalk.sampled(idxs, s.tactile_every)],
                                    photos, s.workers)
    unavailable["tactile_photos"] += [i for i, v in tactile.items() if v is None]
    flags = {str(i): None if v is None else bool(v) for i, v in tactile.items()}  # the defects stage reads these
    labels = {clip: sidewalk.obstacle_labels(sorted({t for c, sc in zip(s.chunks, s.scenes) if c.clip == clip
                                                     for t in sc["obstacle_types"]}), s.standard.path)
              for clip in clips}
    found = run_parallel(lambda c: sidewalk.obstacle_segs(s.api, c, labels[c.clip], s.workers), s.chunks, s.workers)
    found_events = []
    for clip, clip_idxs in clips.items():
        ratios: dict[str, dict[int, float]] = {}
        for chunk, obstacles in zip(s.chunks, found):
            if chunk.clip != clip:
                continue
            unavailable["obstacle_requests"] += [[chunk.idxs[0], label] for label, v in obstacles.items() if v is None]
            for label, by_label in events.chunk_ratios(obstacles, chunk, tactile, by_idx, clip_idxs).items():
                ratios.setdefault(label, {}).update(by_label)
        found_events += [e for e in events.find_events(ratios, clip, clip_idxs)
                         if s.standard.path.fixed or not events.is_fixed(e.label)]  # US: poles at the curb are normal
    for row in run_parallel(lambda e: _issue_row(s, e, by_idx, photos), found_events, s.workers):
        if row is not None:
            db.add_issue(s.conn, s.scan_id, row)
    s.state("sidewalk_unavailable").write_text(json.dumps(unavailable), encoding="utf-8")
    s.state("tactile").write_text(json.dumps(flags), encoding="utf-8")


def _check_videos(s: Scan) -> dict[int, survey_checks.Video]:
    """The videos checks are found on, one per chunk at the photos' own size, keyed by the chunk's first photo idx.
    Made once per scan (work/checks_<first>.mp4); a rerun finds them made."""
    photos = s.photos()

    def one(chunk):
        path = s.work / f"checks_{chunk.idxs[0]:04d}.mp4"
        if path.exists():
            info = media.probe(path)
            size = (info.width, info.height)
        else:
            size = media.images_to_mp4([photos[i] for i in chunk.idxs], path, long_side=CHECK_VIDEO_SIDE)
        return survey_checks.Video(tuple(chunk.idxs), size, chunk.clip)

    return {v.idxs[0]: v for v in run_parallel(one, s.chunks, s.workers)}


def _find_on_videos(s: Scan, checks, videos: dict[int, survey_checks.Video]) -> dict[str, dict[int, str | None]]:
    """SAM's answer for every check found by video on every video: {check id: {video key: text, or None when the
    request failed}}. Each video is uploaded once and asked every check's phrase at once. State find_<id>.json keeps
    the answers for the check's phrase, so a rerun asks only what it does not have (as _run_check does for photos)."""
    answers: dict[str, dict[int, str | None]] = {}
    for check in checks:
        answers[check.id] = {}
        kept = s.state(f"find_{check.id}")
        try:
            saved = json.loads(kept.read_text(encoding="utf-8")) if kept.exists() else {}
            if saved.get("phrase") == check.find:
                answers[check.id] = {int(k): t for k, t in saved["videos"].items() if int(k) in videos}
        except (ValueError, KeyError, AttributeError):
            pass  # a half-written file or a photos check's areas: ask again

    def one_video(key):
        todo = [c for c in checks if key not in answers[c.id]]
        if not todo:
            return {}
        video = videos[key]

        def ask(file_id, check):
            try:
                return s.api.sam_video(check.find, file_id, frames=len(video.idxs))
            except CostCapReached:
                raise
            except Exception:
                return None  # this video is unavailable for this check (spec 14)

        try:
            with s.api.uploaded(s.work / f"checks_{key:04d}.mp4") as file_id:
                texts = run_parallel(lambda c: ask(file_id, c), todo, len(todo))
        except CostCapReached:
            raise
        except Exception:
            texts = [None] * len(todo)  # the upload failed
        return dict(zip((c.id for c in todo), texts))

    try:
        for key, got in zip(videos, run_parallel(one_video, list(videos), s.workers)):
            for check_id, text in got.items():
                answers[check_id][key] = text
    finally:
        for check in checks:
            s.state(f"find_{check.id}").write_text(json.dumps(
                {"phrase": check.find, "videos": {str(k): t for k, t in answers[check.id].items() if t is not None}}),
                encoding="utf-8")
    return answers


def _run_check(s: Scan, check, clips, photos, by_idx, judge=None, dropped: list[dict] | None = None,
               video=None) -> tuple[list[dict], dict]:
    """survey_checks.run_check. A check found by photos reuses the SAM areas a rerun already paid for: state
    find_<id>.json keeps the areas measured for the check's `find` phrase, and is ignored when the phrase has changed.
    Saved even when the run stops part way (cost cap), so the next run only asks for the photos not yet measured. A
    check found by video takes video = (answers, videos) from _find_on_videos."""
    if check.find_by == "video":
        return survey_checks.run_check(s.api, check, clips, photos, by_idx, s.workers, judge=judge,
                                       audit_seed=s.scan_id, dropped=dropped, video=video)
    kept = s.state(f"find_{check.id}")
    known: dict[int, int] = {}
    if kept.exists():
        try:
            saved = json.loads(kept.read_text(encoding="utf-8"))
            if saved.get("phrase") == check.find and saved.get("measure") == "box":  # earlier files hold mask areas
                known = {int(i): a for i, a in saved["areas"].items()}
        except (ValueError, KeyError, AttributeError):
            known = {}  # a half-written file: measure again
    try:
        return survey_checks.run_check(s.api, check, clips, photos, by_idx, s.workers, known=known, judge=judge,
                                       audit_seed=s.scan_id, dropped=dropped)
    finally:
        kept.write_text(json.dumps({"phrase": check.find, "measure": "box",
                                    "areas": {str(i): a for i, a in known.items()}}),
                        encoding="utf-8")


def stage_defects(s: Scan) -> None:
    """KR: S7, missing tactile blocks and covers in the line, checked at the edges of tactile runs and on samples.
    Every standard: its checks (pipeline/survey_checks.py), one row per object found. With only_checks, just those
    checks run: the KR step and the other checks keep their rows and reviews."""
    only = s.only_checks
    unknown = [c for c in only if s.standard.check(c) is None]
    if unknown:
        raise ValueError(f"not a check of the {s.standard.code} standard: {', '.join(unknown)}")
    to_run = [c for c in s.standard.checks if not only or c.id in only]
    # Checks Muse Glimmer judges run back to back, last: between them its GPU would scale down and start again
    # (a few minutes each time). sorted() keeps the standard's order within each group.
    to_run = sorted(to_run, key=lambda c: s.judge is not None and c.judge == "glimmer")
    db.clear_issues(s.conn, s.scan_id, (*(() if only else defects.ISSUE_TYPES), *(c.id for c in to_run)))
    by_idx, photos, clips = s.by_idx(), s.photos(), s.clips()
    failed: list[int] = []
    checks: list[dict] = []
    if s.standard.tactile_defects and not only:
        flags = {int(k): v for k, v in json.loads(s.state("tactile").read_text(encoding="utf-8")).items()}
        for clip_idxs in clips.values():
            rows, bad, clip_checks = defects.find_defects(s.api, defects.candidates(flags, clip_idxs), photos, by_idx,
                                                          clip_idxs, s.workers)
            failed += bad
            checks += clip_checks
            for row in rows:
                db.add_issue(s.conn, s.scan_id, row)
    kept = s.state("checks_unavailable")
    unavailable: dict[str, dict] = json.loads(kept.read_text(encoding="utf-8")) if only and kept.exists() else {}
    by_video = [c for c in to_run if c.find_by == "video"]
    videos = _check_videos(s) if by_video else {}
    answers = _find_on_videos(s, by_video, videos)

    def one(check):
        judge = s.judge if s.judge is not None and check.judge == "glimmer" else None
        gone: list[dict] = []
        rows, missing = _run_check(s, check, clips, photos, by_idx, judge, dropped=gone,
                                   video=(answers[check.id], videos) if check.find_by == "video" else None)
        if s.second_opinion is not None:
            rows = second_opinion.apply(s.second_opinion, check, rows, photos, s.workers)
        return rows, missing, gone

    # The checks run at once (the API client keeps the requests in flight under its limit); Glimmer's run after, back
    # to back. Rows are written in to_run's order.
    plain = [c for c in to_run if not (s.judge is not None and c.judge == "glimmer")]
    done = dict(zip((c.id for c in plain), run_parallel(one, plain, len(plain))))
    for check in to_run:
        if check.id not in done:
            done[check.id] = one(check)
    for check in to_run:
        rows, missing, gone = done[check.id]
        unavailable[check.id] = missing
        for row in rows:
            db.add_issue(s.conn, s.scan_id, row)
        for pick in gone:
            db.add_dropped(s.conn, s.scan_id, pick)
    if not only:
        s.state("defects_unavailable").write_text(json.dumps(failed), encoding="utf-8")
        s.state("defects_checks").write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
    s.state("checks_unavailable").write_text(json.dumps(unavailable), encoding="utf-8")


def evidence_name(scan_id: int, kind: str, row_id: int, image_id: str) -> str:
    """The evidence file of one row. The photo's id is part of the name: a rerun gives new rows the ids of deleted ones
    (SQLite reuses the highest), so without it a rerun wrote over the files of a database copy that was later put back,
    and six rows showed another row's photo (scan 4's litter cans, 2026-10-28)."""
    return f"scan{scan_id}_{kind}{row_id}_{image_id}.jpg"


def stage_evidence(s: Scan, kinds: tuple[str, ...] = ("entrance", "issue")) -> None:
    """Blurred, attributed evidence photos for rows that have none yet. A row whose photo failed keeps an error
    record and is tried again on the next run; the stage then fails, so the failure is visible."""
    out = Path(s.run_dir) / "evidence"
    by_idx, photos = s.by_idx(), s.photos()
    todo = ([("entrance", r) for r in db.entrances(s.conn, s.scan_id) if not (r["evidence"] or {}).get("image")]
            if "entrance" in kinds else [])
    todo += ([("issue", r) for r in db.issues(s.conn, s.scan_id) if not (r["evidence"] or {}).get("image")]
             if "issue" in kinds else [])

    def one(item):
        kind, row = item
        frame = by_idx[row["frame_idx"]]
        try:
            photo = _original_photo(frame["image_id"]) if kind == "entrance" else photos[row["frame_idx"]]
            return evidence.make_evidence(s.api, photo, frame, out / evidence_name(s.scan_id, kind, row["id"],
                                                                                   frame["image_id"]))
        except CostCapReached:
            raise
        except Exception as exc:  # no photo is stored without blurring; this row is retried on the next run
            return {"error": error_text(exc)}

    records = run_parallel(one, todo, s.workers)
    for (kind, row), record in zip(todo, records):
        (db.update_entrance if kind == "entrance" else db.update_issue)(s.conn, row["id"], evidence=record)
    failed = sum("error" in r for r in records)
    if failed:
        raise RuntimeError(f"evidence failed for {failed} of {len(todo)} rows; they are retried on the next run")


def stage_sidewalk_evidence(s: Scan) -> None:
    """Evidence photos for the sidewalk rows first: the main layer keeps its photos even if the cost cap stops the
    entrance stage."""
    stage_evidence(s, kinds=("issue",))


STAGE_FUNCS = {"collect": stage_collect, "chunks": stage_chunks, "scene": stage_scene,
               "sidewalk": stage_sidewalk, "defects": stage_defects,
               "sidewalk_evidence": stage_sidewalk_evidence, "entrances": stage_entrances,
               "evidence": stage_evidence}


def _load(s: Scan, stage: str) -> None:
    """Bring back a finished stage's results for the stages after it."""
    if stage == "collect":
        s.frames = db.frames(s.conn, s.scan_id)
    elif stage == "chunks":
        s.chunks = [scene.Chunk(**c) for c in json.loads(s.state("chunks").read_text(encoding="utf-8"))]
    elif stage == "scene":
        s.scenes = json.loads(s.state("scene").read_text(encoding="utf-8"))


def unavailable(work: Path) -> dict:
    """Counts of what could not be analysed (spec 14), from the stage state files."""
    def load(name: str, default):
        path = Path(work) / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    side = load("sidewalk_unavailable", {"tactile_photos": [], "obstacle_requests": []})
    return {"scene_chunks": sum("error" in c for c in load("scene", [])),
            "tactile_photos": len(side["tactile_photos"]), "obstacle_requests": len(side["obstacle_requests"]),
            "defect_checks": len(load("defects_unavailable", [])),
            "check_requests": sum(len(v["find"]) + len(v["ask"]) for v in load("checks_unavailable", {}).values())}


def summary(conn, scan_id: int, work: Path | None = None) -> dict:
    scan = db.get_scan(conn, scan_id)
    std = survey_items.load(scan["jurisdiction"])
    ents, iss = db.entrances(conn, scan_id), db.issues(conn, scan_id)
    photos = scan["image_count"]
    per_100 = 100 / photos if photos else 0.0
    return {"scan_id": scan_id, "status": scan["status"], "error": scan["error"], "photos": photos,
            "entrances": len(ents),
            "entrances_needing_review": sum(e["review_status"] == "needs_review" for e in ents),
            "issues": len(iss), "defects": sum(std.is_defect(i) for i in iss if i["review_status"] != "rejected"),
            "cost_usd": round(scan["cost_usd"], 4),
            "processing_seconds": round(scan["processing_seconds"], 1),
            "cost_per_100_photos": round(scan["cost_usd"] * per_100, 4),
            "seconds_per_100_photos": round(scan["processing_seconds"] * per_100, 1),
            "unavailable": unavailable(work) if work else None,
            "stage_status": scan["stage_status"]}


def run_scan(s: Scan) -> dict:
    refuse_in_worker()
    conn, scan_id = s.conn, s.scan_id
    started = time.time()
    before = db.get_scan(conn, scan_id)
    if s.standard is None:
        s.standard = survey_items.load(before["jurisdiction"])
    meter = getattr(s.api, "meter", None)
    if before["status"] == "stopped_cost_cap" and meter and meter.cap_usd - meter.spent_usd < RESUME_MIN_USD:
        # resuming would clear the stopped stage's saved rows and stop again at once (spec 13: the cap is per scan)
        db.update_scan(conn, scan_id, error=f"cost cap ${meter.cap_usd:.2f} already reached; start a smaller scan")
        return summary(conn, scan_id, s.work)
    db.update_scan(conn, scan_id, status="running", error=None)
    status, error = "failed", "interrupted"
    stage_error = None
    ran: set[str] = set()
    try:
        for stage in STAGES:
            done = db.get_scan(conn, scan_id)["stage_status"].get(stage) == "done"
            if done and not (RERUN_AFTER.get(stage, set()) & ran):
                _load(s, stage)
                continue
            ran.add(stage)
            db.set_stage(conn, scan_id, stage, "running")
            try:
                STAGE_FUNCS[stage](s)
            except (CostCapReached, RunAborted):
                db.set_stage(conn, scan_id, stage, "stopped")
                raise
            except Exception as exc:
                db.set_stage(conn, scan_id, stage, f"failed: {error_text(exc)}")
                if stage not in INDEPENDENT:
                    raise
                stage_error = error_text(exc)
            else:
                db.set_stage(conn, scan_id, stage, "done")
            if meter:
                db.update_scan(conn, scan_id, cost_usd=meter.spent_usd)  # a killed run keeps what it spent
        status, error = ("failed", stage_error) if stage_error else ("done", None)
    except CostCapReached as exc:
        status, error = "stopped_cost_cap", str(exc)
    except RunAborted as exc:
        status, error = "failed", str(exc)
    except Exception as exc:
        status, error = "failed", error_text(exc)
    finally:
        db.update_scan(conn, scan_id, status=status, error=error,
                       cost_usd=meter.spent_usd if meter else before["cost_usd"],
                       processing_seconds=before["processing_seconds"] + time.time() - started)
    return summary(conn, scan_id, s.work)
