"""HTTP API and the built web app (spec 5, 6, 13). Run with:

  uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8000

Starting a survey passes the spec 13 limits (invite code if set, area size, surveys per address per day). Scans run
one at a time on the ScanWorker thread, and the pipeline's own caps ($3 per scan, $10 per day) still apply.
"""
import logging
import re
import threading
from contextlib import asynccontextmanager, closing
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline import collect, config, db, mapillary, survey_items
from pipeline.runner import RESUME_MIN_USD
from server import limits, review, views
from server.jobs import ScanWorker
from server.settings import Settings, load
from server.store import DatasetStore

log = logging.getLogger(__name__)

EVIDENCE_NAME = re.compile(r"scan\d+_(entrance|issue)\d+\.jpg", re.ASCII)  # used with fullmatch


class BboxBody(BaseModel):
    bbox: list[float]


class ScanBody(BaseModel):
    bbox: list[float]
    invite_code: str | None = None


class EntranceReview(BaseModel):
    action: Literal["confirm", "correct"]
    seconds: float = Field(ge=0, le=3600)
    step_count: int | None = Field(default=None, ge=0, le=50)

    model_config = {"extra": "forbid"}  # spec revision 4: the step count is the only field a reviewer edits


class IssueReview(BaseModel):
    action: Literal["confirm", "reject"]
    seconds: float = Field(ge=0, le=3600)

    model_config = {"extra": "forbid"}


def create_app(settings: Settings | None = None, worker=None, preview=None, store=None) -> FastAPI:
    settings = settings or load()
    if settings.ip_hash_salt == "threshold":
        log.warning("IP_HASH_SALT is not set; set a random salt in production")
    run_dir = config.run_dir(settings.run_name)
    db_path = run_dir / "threshold.db"
    if store is None and settings.hf_data_repo:
        store = DatasetStore(settings.hf_data_repo, run_dir)
    worker = worker or ScanWorker(db_path, run_dir, on_done=(lambda _: store.request_save()) if store else None)
    preview = preview or collect.preview

    start_lock = threading.Lock()  # the per-address check, the new scan and its address tag happen as one step
    review_lock = threading.Lock()  # the "not reviewed yet" check and the review's write happen as one step
    demo_ids = {scan_id for scan_id, _ in settings.demo_scans}

    def check_reviewable(row: dict) -> None:
        if row["scan_id"] in demo_ids:
            raise HTTPException(403, "Demo surveys are read-only")
        if row["review_status"] in ("verified", "rejected"):
            raise HTTPException(409, "This item has already been reviewed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store:
            store.restore()  # before anything opens the database
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Fieldless", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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
        # PROXY_HOPS.
        forwarded = [p for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        return {"ok": True, "forwarded_for_entries": len(forwarded)}

    @app.get("/api/config")
    def get_config():
        return {"invite_required": settings.invite_code is not None, "max_bbox_deg2": settings.max_bbox_deg2}

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
            return preview(bbox, config.photo_budget())
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
            limits.check_daily_scans(conn, hashed, settings.scans_per_ip_per_day)
            scan_id = db.create_scan(conn, "mapillary", bbox, survey_items.standard_for(bbox))
            db.update_scan(conn, scan_id, client_ip_hash=hashed)
        worker.submit(scan_id)
        return {"id": scan_id}

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: int):
        with connect() as conn:
            scan = found(db.get_scan, conn, scan_id, "scan")
            return views.scan_json(scan, db.entrances(conn, scan_id), db.issues(conn, scan_id),
                                   km=views.surveyed_km(db.frames(conn, scan_id)))

    @app.get("/api/scans/{scan_id}/entrances")
    def get_entrances(scan_id: int):
        with connect() as conn:
            found(db.get_scan, conn, scan_id, "scan")
            return [views.entrance_json(row) for row in db.entrances(conn, scan_id)]

    @app.get("/api/scans/{scan_id}/issues")
    def get_issues(scan_id: int):
        with connect() as conn:
            found(db.get_scan, conn, scan_id, "scan")
            rows = [row for row in db.issues(conn, scan_id) if row["review_status"] != "rejected"]
            return [views.issue_json(row) for row in sorted(rows, key=views.issue_order)]

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
            return views.entrance_json(db.get_entrance(conn, entrance_id))

    @app.post("/api/issues/{issue_id}/review")
    def review_issue(issue_id: int, body: IssueReview):
        with review_lock, connect() as conn:
            row = found(db.get_issue, conn, issue_id, "sidewalk issue")
            check_reviewable(row)
            db.update_issue(conn, issue_id, **review.issue_update(row, body.action, body.seconds, db.now()))
            db.add_review_seconds(conn, row["scan_id"], body.seconds)
            if store:
                store.request_save()
            return views.issue_json(db.get_issue(conn, issue_id))

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
