"""HTTP API and the built web app (spec 5, 6, 13). Run with:

  uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8000

Starting a survey passes the spec 13 limits (invite code if set, area size, surveys per address per day). Scans run
one at a time on the ScanWorker thread, and the pipeline's own caps ($3 per scan, $10 per day) still apply.
"""
import gzip
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager, closing
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from assist import report
from pipeline import collect, config, db, mapillary, sam_output, survey_items
from pipeline.runner import RESUME_MIN_USD
from server import files, limits, review, views
from server.jobs import ScanWorker
from server.settings import Settings, load
from server.store import DatasetStore, folder_store

log = logging.getLogger(__name__)

# used with fullmatch; scans before 2026-10-28 22:00 KST named the files without the photo id (runner.evidence_name)
EVIDENCE_NAME = re.compile(r"scan\d+_(entrance|issue)\d+(_\d+)?\.jpg", re.ASCII)


class BboxBody(BaseModel):
    bbox: list[float]


class ScanBody(BaseModel):
    bbox: list[float]
    invite_code: str | None = None


class EntranceReview(BaseModel):
    action: Literal["confirm", "correct", "field_check"]
    seconds: float = Field(ge=0, le=3600)
    step_count: int | None = Field(default=None, ge=0, le=50)

    model_config = {"extra": "forbid"}  # spec revision 4: the step count is the only field a reviewer edits


class IssueReview(BaseModel):
    action: Literal["confirm", "reject", "answer", "field_check"]
    seconds: float = Field(ge=0, le=3600)
    answer: Literal["yes", "no"] | None = None  # only with action "answer", on a survey check row

    model_config = {"extra": "forbid"}


TILE_CACHE_S = 3600  # coverage changes when people upload; an hour old is fine for choosing an area
TILE_CACHE_BYTES = 64_000_000  # compressed tiles kept; a dense zoom 13 tile is about 1 MB, a third of that gzipped


def create_app(settings: Settings | None = None, worker=None, preview=None, store=None,
               tile=mapillary.coverage_tile) -> FastAPI:
    settings = settings or load()
    if settings.ip_hash_salt == "threshold":
        log.warning("IP_HASH_SALT is not set; set a random salt in production")
    run_dir = config.run_dir(settings.run_name)
    db_path = run_dir / "threshold.db"
    if store is None and settings.hf_data_repo:
        store = DatasetStore(settings.hf_data_repo, run_dir)
    elif store is None and settings.backup_dir:
        store = folder_store(settings.backup_dir, run_dir)
    worker = worker or ScanWorker(db_path, run_dir, on_done=(lambda _: store.request_save()) if store else None,
                                  public_cap_usd=settings.public_cost_cap_usd,
                                  glimmer_daily_usd=settings.glimmer_daily_usd,
                                  glimmer_total_usd=settings.glimmer_total_usd)
    preview = preview or collect.preview

    start_lock = threading.Lock()  # the per-address check, the new scan and its address tag happen as one step
    review_lock = threading.Lock()  # the "not reviewed yet" check and the review's write happen as one step
    demo_ids = {scan_id for scan_id, _ in settings.demo_scans}

    def check_reviewable(row: dict) -> None:
        if row["scan_id"] in demo_ids:
            raise HTTPException(403, "Demo surveys are read-only")
        if row["review_status"] in review.REVIEWED:
            raise HTTPException(409, "This item has already been reviewed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store:
            store.restore()  # before anything opens the database
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Fieldless", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker

    def connect():
        return closing(db.connect(db_path))

    def found(getter, conn, key: int, what: str) -> dict:
        try:
            return getter(conn, key)
        except KeyError:
            raise HTTPException(404, f"{what} {key} not found") from None

    @app.exception_handler(limits.LimitError)
    async def limit_error(request: Request, exc: limits.LimitError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = first.get("msg", "invalid request")
        return JSONResponse(status_code=422, content={"detail": f"{where}: {message}" if where else message})

    @app.middleware("http")
    async def html_is_never_cached(request: Request, call_next):
        """index.html names the hashed asset files of one build, so a stale copy after a redeploy is a blank page."""
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/health")
    def health(request: Request):
        # How many X-Forwarded-For entries reach the server (a count, no addresses): checked once per host to set
        # PROXY_HOPS. mask_decoding says whether SAM masks are decoded in worker processes on this host.
        forwarded = [p for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        return {"ok": True, "forwarded_for_entries": len(forwarded), "mask_decoding": sam_output.decoding()}

    @app.get("/api/config")
    def get_config():
        return {"invite_required": settings.invite_code is not None, "max_bbox_deg2": settings.max_bbox_deg2,
                "coverage_tiles": settings.coverage_tiles, "report": True}  # report: the survey report page

    tiles: dict[tuple[int, int, int], tuple[float, bytes | None]] = {}  # (z, x, y) -> (fetched at, gzipped body)
    tiles_lock = threading.Lock()

    @app.get("/api/mapillary/tiles/{z}/{x}/{y}")
    def coverage_tile(z: int, x: int, y: int, request: Request):
        """Mapillary's coverage tile, so the new survey screen can draw where photos exist (docs/specs
        2026-10-28-design-pass2-design.md). The token stays on the server; tiles are kept TILE_CACHE_S."""
        if not settings.coverage_tiles or z not in mapillary.COVERAGE_ZOOMS or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise HTTPException(404, "no such tile")
        now = time.time()
        with tiles_lock:
            kept = tiles.get((z, x, y))
        if kept is None or now - kept[0] > TILE_CACHE_S:
            try:
                body = tile(z, x, y)
            except mapillary.MapillaryError:
                raise HTTPException(502, "Mapillary coverage is not available right now") from None
            kept = (now, None if body is None else gzip.compress(body, 6))
            with tiles_lock:
                tiles[(z, x, y)] = kept
                while sum(len(b or b"") for _, b in tiles.values()) > TILE_CACHE_BYTES and len(tiles) > 1:
                    tiles.pop(min(tiles, key=lambda k: tiles[k][0]))  # the oldest
        if kept[1] is None:
            return Response(status_code=204)
        headers = {"Cache-Control": f"public, max-age={TILE_CACHE_S}", "Vary": "Accept-Encoding"}
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(kept[1], media_type="application/vnd.mapbox-vector-tile",
                            headers={**headers, "Content-Encoding": "gzip"})
        return Response(gzip.decompress(kept[1]), media_type="application/vnd.mapbox-vector-tile", headers=headers)

    @app.get("/api/demo")
    def demo():
        out = []
        with connect() as conn:
            for scan_id, name in settings.demo_scans:
                try:
                    scan = db.get_scan(conn, scan_id)
                except KeyError:
                    continue
                out.append({"scan_id": scan_id, "name": name, "bbox": scan["bbox"]})
        return out

    @app.post("/api/preview")
    def post_preview(body: BboxBody):
        bbox = limits.check_bbox(body.bbox, settings.max_bbox_deg2)
        try:
            std = survey_items.load(survey_items.standard_for(bbox))
            checks = len(std.checks)
            found = preview(bbox, config.photo_budget(checks=checks), checks=checks)
            # what the survey would look for, and its spending cap, for the preview step (ui-redesign 3.5)
            return {**found, "standard": std.code, "items": views.items_json(std), "cap_usd": config.SCAN_COST_CAP_USD}
        except (mapillary.MapillaryError, httpx.HTTPError, ValueError):  # ValueError: a reply that is not JSON
            log.exception("preview failed")
            raise HTTPException(502, "Mapillary did not answer. Try again in a minute.") from None

    @app.post("/api/scans", status_code=202)
    def post_scan(body: ScanBody, request: Request):
        limits.check_invite(body.invite_code, settings.invite_code)
        bbox = limits.check_bbox(body.bbox, settings.max_bbox_deg2)
        peer = request.client.host if request.client else None
        hashed = limits.ip_hash(limits.client_ip(request.headers, peer), settings.ip_hash_salt)
        with start_lock, connect() as conn:
            if config.DAILY_COST_CAP_USD - db.daily_cost(conn, db.now()[:10]) < RESUME_MIN_USD:
                raise HTTPException(503, "Today's survey budget is used up. Try again tomorrow (UTC).")
            cap = settings.public_cost_cap_usd
            if cap is not None and cap - db.public_cost(conn) < RESUME_MIN_USD:
                raise HTTPException(503, "The live demo's survey budget is used up; the finished surveys stay open.")
            limits.check_daily_scans(conn, hashed, settings.scans_per_ip_per_day)
            scan_id = db.create_scan(conn, "mapillary", bbox, survey_items.standard_for(bbox))
            db.update_scan(conn, scan_id, client_ip_hash=hashed)
        worker.submit(scan_id)
        return {"id": scan_id}

    def city_check(scan_id: int) -> dict | None:
        """The offline comparison with the city's records (eval, docs/specs/2026-10-28-ui-redesign-design.md 4.1),
        if one was made for this scan. A file that cannot be read is left out, not an error for the visitor."""
        path = run_dir / f"city_check_scan{scan_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("could not read %s", path)
            return None
        return data if isinstance(data, dict) else None

    def headings(conn, scan_id: int) -> dict[int, float | None]:
        return {f["idx"]: f.get("compass") for f in db.frames(conn, scan_id)}

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: int):
        with connect() as conn:
            scan = found(db.get_scan, conn, scan_id, "scan")
            return views.scan_json(scan, db.entrances(conn, scan_id), db.issues(conn, scan_id),
                                   km=views.surveyed_km(db.frames(conn, scan_id)), city_check=city_check(scan_id),
                                   dropped=views.dropped_count(scan, len(db.dropped(conn, scan_id))))

    @app.get("/api/scans/{scan_id}/walks")
    def get_walks(scan_id: int):
        """Each walk's photo positions in order, for the map's coverage line."""
        with connect() as conn:
            found(db.get_scan, conn, scan_id, "scan")
            walks: dict[int, list] = {}
            for f in db.frames(conn, scan_id):
                if f["lat"] is not None and f["lng"] is not None:
                    walks.setdefault(f["clip"], []).append([f["lng"], f["lat"]])
            return [{"clip": clip, "points": points} for clip, points in sorted(walks.items())]

    @app.get("/api/scans/{scan_id}/entrances")
    def get_entrances(scan_id: int):
        with connect() as conn:
            found(db.get_scan, conn, scan_id, "scan")
            heading = headings(conn, scan_id)
            return [views.entrance_json(row, heading=heading.get(row["frame_idx"]))
                    for row in db.entrances(conn, scan_id)]

    @app.get("/api/scans/{scan_id}/issues")
    def get_issues(scan_id: int):
        with connect() as conn:
            std = views.standard_of(found(db.get_scan, conn, scan_id, "scan"))
            heading = headings(conn, scan_id)
            rows = [row for row in db.issues(conn, scan_id) if row["review_status"] != "rejected"]
            return [views.issue_json(row, std=std, heading=heading.get(row["frame_idx"]))
                    for row in sorted(rows, key=lambda r: views.issue_order(r, std))]

    def survey_data(conn, scan_id: int) -> tuple[dict, dict]:
        """(scan, scan_data) for a finished survey's report and files; 404 if there is no such scan, 409 while it
        runs."""
        scan = found(db.get_scan, conn, scan_id, "scan")
        if scan["status"] in ("queued", "running"):
            raise HTTPException(409, "The survey is still running; its files are ready when it finishes.")
        return scan, {"scan": scan, "frames": db.frames(conn, scan_id), "issues": db.issues(conn, scan_id),
                      "entrances": db.entrances(conn, scan_id), "dropped": db.dropped(conn, scan_id),
                      "city_check": city_check(scan_id)}

    @app.get("/api/scans/{scan_id}/files.zip")
    def get_files(scan_id: int):
        with connect() as conn:
            scan, data = survey_data(conn, scan_id)
            notes = db.report_text(conn, scan_id)
        body = files.survey_zip(data, views.standard_of(scan), run_dir / "evidence",
                                area=dict(settings.demo_scans).get(scan_id), notes=notes)
        return Response(body, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="fieldless-survey-{scan_id}.zip"'})

    @app.get("/api/scans/{scan_id}/report")
    def get_report(scan_id: int):
        """The survey report document (assist.report_doc); its photos are the app's evidence photos, two folders up
        from this path, as on the static site."""
        with connect() as conn:
            scan, data = survey_data(conn, scan_id)
            notes = db.report_text(conn, scan_id)
        page = report.document(data, views.standard_of(scan), area=dict(settings.demo_scans).get(scan_id),
                               notes=notes, photo_base="../../evidence/")
        return HTMLResponse(page)

    @app.get("/api/evidence/{name}")
    def get_evidence(name: str):
        if not EVIDENCE_NAME.fullmatch(name):
            raise HTTPException(404, "no such evidence photo")
        path = run_dir / "evidence" / name
        if not path.is_file():
            raise HTTPException(404, "no such evidence photo")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/entrances/{entrance_id}/review")
    def review_entrance(entrance_id: int, body: EntranceReview):
        with review_lock, connect() as conn:
            row = found(db.get_entrance, conn, entrance_id, "entrance")
            check_reviewable(row)
            if body.action == "correct" and body.step_count is None:
                raise HTTPException(422, "A correction needs the step count")
            changes = {"step_count": body.step_count}
            db.update_entrance(conn, entrance_id,
                               **review.entrance_update(row, body.action, changes, body.seconds, db.now()))
            db.add_review_seconds(conn, row["scan_id"], body.seconds)
            if store:
                store.request_save()
            return entrance_out(conn, entrance_id)

    @app.post("/api/issues/{issue_id}/review")
    def review_issue(issue_id: int, body: IssueReview):
        with review_lock, connect() as conn:
            row = found(db.get_issue, conn, issue_id, "sidewalk issue")
            check_reviewable(row)
            std = views.standard_of(db.get_scan(conn, row["scan_id"]))
            if body.action == "answer":
                if std.check(row["issue_type"]) is None:
                    raise HTTPException(422, "Only a survey check takes an answer")
                if body.answer is None:
                    raise HTTPException(422, "An answer needs yes or no")
                if std.check(row["issue_type"]).inventory and body.answer == "no":
                    raise HTTPException(422, "An inventory item is confirmed with yes; reject it if it is not one")
            db.update_issue(conn, issue_id,
                            **review.issue_update(row, body.action, body.seconds, db.now(), body.answer))
            db.add_review_seconds(conn, row["scan_id"], body.seconds)
            if store:
                store.request_save()
            return issue_out(conn, issue_id)

    def entrance_out(conn, entrance_id: int) -> dict:
        row = db.get_entrance(conn, entrance_id)
        return views.entrance_json(row, heading=db.frame_compass(conn, row["scan_id"], row["frame_idx"]))

    def issue_out(conn, issue_id: int) -> dict:
        row = db.get_issue(conn, issue_id)
        std = views.standard_of(db.get_scan(conn, row["scan_id"]))
        return views.issue_json(row, std=std, heading=db.frame_compass(conn, row["scan_id"], row["frame_idx"]))

    def take_back(row: dict, kind: str) -> tuple[dict, float]:
        """The fields that undo the row's last decision (review.undo) and the seconds to take off the review time."""
        if row["scan_id"] in demo_ids:
            raise HTTPException(403, "Demo surveys are read-only")
        try:
            return review.undo(row, kind, db.now())
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/entrances/{entrance_id}/review/undo")
    def undo_entrance(entrance_id: int):
        with review_lock, connect() as conn:
            row = found(db.get_entrance, conn, entrance_id, "entrance")
            fields, seconds = take_back(row, "entrance")
            db.update_entrance(conn, entrance_id, **fields)
            db.add_review_seconds(conn, row["scan_id"], -seconds)
            if store:
                store.request_save()
            return entrance_out(conn, entrance_id)

    @app.post("/api/issues/{issue_id}/review/undo")
    def undo_issue(issue_id: int):
        with review_lock, connect() as conn:
            row = found(db.get_issue, conn, issue_id, "sidewalk issue")
            fields, seconds = take_back(row, "issue")
            db.update_issue(conn, issue_id, **fields)
            db.add_review_seconds(conn, row["scan_id"], -seconds)
            if store:
                store.request_save()
            return issue_out(conn, issue_id)

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
