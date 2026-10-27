"""HTTP API and the built web app (spec 5, 6, 13). Run with:

  uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8000

Starting a survey passes the spec 13 limits (invite code if set, area size, surveys per address per day). Scans run
one at a time on the ScanWorker thread, and the pipeline's own caps ($3 per scan, $10 per day) still apply.
"""
import re
import threading
from contextlib import asynccontextmanager, closing
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline import collect, config, db
from server import limits, review, views
from server.jobs import ScanWorker
from server.settings import Settings, load

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


def create_app(settings: Settings | None = None, worker=None, preview=None) -> FastAPI:
    settings = settings or load()
    run_dir = config.run_dir(settings.run_name)
    db_path = run_dir / "threshold.db"
    worker = worker or ScanWorker(db_path, run_dir)
    preview = preview or collect.preview

    start_lock = threading.Lock()  # the per-address check, the new scan and its address tag happen as one step
    demo_ids = {scan_id for scan_id, _ in settings.demo_scans}

    def check_reviewable(scan_id: int) -> None:
        if scan_id in demo_ids:
            raise HTTPException(403, "Demo surveys are read-only")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Dotline", lifespan=lifespan)

    def connect():
        return closing(db.connect(db_path))

    def found(getter, conn, key: int, what: str) -> dict:
        try:
            return getter(conn, key)
        except KeyError:
            raise HTTPException(404, f"{what} {key} not found") from None

    def add_review_time(conn, scan_id: int, seconds: float) -> None:
        scan = db.get_scan(conn, scan_id)
        db.update_scan(conn, scan_id, review_seconds=scan["review_seconds"] + seconds)

    @app.exception_handler(limits.LimitError)
    async def limit_error(request: Request, exc: limits.LimitError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = first.get("msg", "invalid request")
        return JSONResponse(status_code=422, content={"detail": f"{where}: {message}" if where else message})

    @app.get("/api/health")
    def health():
        return {"ok": True}

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
        return preview(bbox, config.photo_budget())

    @app.post("/api/scans", status_code=202)
    def post_scan(body: ScanBody, request: Request):
        limits.check_invite(body.invite_code, settings.invite_code)
        bbox = limits.check_bbox(body.bbox, settings.max_bbox_deg2)
        peer = request.client.host if request.client else None
        hashed = limits.ip_hash(limits.client_ip(request.headers, peer), settings.ip_hash_salt)
        with start_lock, connect() as conn:
            limits.check_daily_scans(conn, hashed, settings.scans_per_ip_per_day)
            scan_id = db.create_scan(conn, "mapillary", bbox)
            db.update_scan(conn, scan_id, client_ip_hash=hashed)
        worker.submit(scan_id)
        return {"id": scan_id}

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: int):
        with connect() as conn:
            scan = found(db.get_scan, conn, scan_id, "scan")
            return views.scan_json(scan, db.entrances(conn, scan_id), db.issues(conn, scan_id))

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
        with connect() as conn:
            row = found(db.get_entrance, conn, entrance_id, "entrance")
            check_reviewable(row["scan_id"])
            if body.action == "correct" and body.step_count is None:
                raise HTTPException(422, "A correction needs the step count")
            changes = {"step_count": body.step_count}
            db.update_entrance(conn, entrance_id,
                               **review.entrance_update(row, body.action, changes, body.seconds, db.now()))
            add_review_time(conn, row["scan_id"], body.seconds)
            return views.entrance_json(db.get_entrance(conn, entrance_id))

    @app.post("/api/issues/{issue_id}/review")
    def review_issue(issue_id: int, body: IssueReview):
        with connect() as conn:
            row = found(db.get_issue, conn, issue_id, "sidewalk issue")
            check_reviewable(row["scan_id"])
            db.update_issue(conn, issue_id, **review.issue_update(row, body.action, body.seconds, db.now()))
            add_review_time(conn, row["scan_id"], body.seconds)
            return views.issue_json(db.get_issue(conn, issue_id))

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
