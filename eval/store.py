"""Read one scan from a run's threshold.db without writing to it (plan 3)."""
import sqlite3
from pathlib import Path
from urllib.parse import quote

from pipeline import db


def open_ro(path: Path) -> sqlite3.Connection:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{quote(path.as_posix(), safe='/:')}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def dropped(conn: sqlite3.Connection, scan_id: int) -> list[dict]:
    """The picks the models set aside as not the target (pipeline.db.dropped); none in a database made before the
    pipeline kept them."""
    try:
        return db.dropped(conn, scan_id)
    except sqlite3.OperationalError:  # no dropped_pick table yet
        return []


def load_scan(conn: sqlite3.Connection, scan_id: int) -> dict:
    return {"scan": db.get_scan(conn, scan_id), "frames": db.frames(conn, scan_id),
            "issues": db.issues(conn, scan_id), "entrances": db.entrances(conn, scan_id),
            "dropped": dropped(conn, scan_id)}
