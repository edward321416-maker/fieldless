"""One Modal container in a test: its own disk (a THRESHOLD_DATA folder that goes away with it), the shared Volume (a
folder that stays), and the real app, store and worker wired as deploy/modal_app.py wires them."""
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from pipeline import config, db
from server.app import create_app
from server.settings import Settings
from server.store import DB_NAME, DatasetStore, FolderBackup

RUN = "demo"
SEATTLE = [-122.32, 47.61, -122.3185, 47.611]
DEMO_ID = 1  # the finished survey every container must keep open


class Container:
    def __init__(self, tmp: Path, name: str, monkeypatch, delay_s: float = 3600.0, **settings):
        self.disk = Path(tmp) / name
        self.volume = Path(tmp) / "volume"
        monkeypatch.setenv("THRESHOLD_DATA", str(self.disk))
        self.run_dir = config.run_dir(RUN)
        self.db_path = self.run_dir / DB_NAME
        backup = FolderBackup(self.volume)
        self.store = DatasetStore(str(self.volume), self.run_dir, api=backup, download=backup.download, delay_s=delay_s)
        self.settings = Settings(**{"run_name": RUN, "backup_dir": self.volume, "public_cost_cap_usd": 40.0,
                                    "scans_per_ip_per_day": 0, "demo_scans": ((DEMO_ID, "Example"),),
                                    "static_dir": self.disk / "no-web", **settings})
        self.app = create_app(self.settings, store=self.store, preview=lambda *a, **k: {})
        self.worker = self.app.state.worker
        self.client = TestClient(self.app)

    def boot(self) -> "Container":
        """What the app's lifespan does at start, without its shutdown: a test can then drop the container the way a
        crash does, with nothing run on the way out."""
        self.store.restore()
        self.worker.start()
        return self

    def conn(self):
        return closing(db.connect(self.db_path))

    def seed_finished_survey(self) -> None:
        """The example survey, finished and backed up, as `modal_app.py --seed` leaves it."""
        with self.conn() as conn:
            scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
            db.update_scan(conn, scan_id, status="done", image_count=60, cost_usd=1.5)
        evidence = self.run_dir / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "scan1_issue1_123.jpg").write_bytes(b"\xff\xd8" + b"photo" * 2000 + b"\xff\xd9")
        self.store.save_now()


def wait_for(condition, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def backed_up_scans(volume: Path) -> dict[int, str]:
    """{scan id: status} of the database on the Volume."""
    with closing(sqlite3.connect(Path(volume) / DB_NAME)) as conn:
        return dict(conn.execute("SELECT id, status FROM scan ORDER BY id").fetchall())


class Hanging:
    """A job that spends, then never returns while the test runs. release() lets its thread end."""

    def __init__(self, cost_usd: float, db_path: Path, update=db.update_scan):
        self.cost_usd, self.db_path, self.update = cost_usd, db_path, update
        self.started, self.gate = threading.Event(), threading.Event()

    def __call__(self, job_id: int) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self.update(conn, job_id, status="running", cost_usd=self.cost_usd)
        self.started.set()
        self.gate.wait(30)

    def release(self) -> None:
        self.gate.set()
