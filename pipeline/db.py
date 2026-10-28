"""SQLite store for scans, frames, entrances and sidewalk issues (spec section 12).
Rows hold facts only; display labels are made on request (spec 7.4 step 8). The entrance columns for rise, ramp,
door and handrail predate spec revision 4 (steps only) and stay empty."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    bbox TEXT,
    jurisdiction TEXT NOT NULL DEFAULT 'KR',
    sequence_ids TEXT NOT NULL DEFAULT '[]',
    image_count INTEGER NOT NULL DEFAULT 0,
    captured_from TEXT,
    captured_to TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage_status TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0,
    processing_seconds REAL NOT NULL DEFAULT 0,
    review_seconds REAL NOT NULL DEFAULT 0,
    client_ip_hash TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS dropped_pick (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    issue_type TEXT NOT NULL,
    frame_idx INTEGER NOT NULL,
    frame_start INTEGER,
    frame_end INTEGER,
    lat REAL,
    lng REAL,
    captured_at TEXT,
    audited INTEGER NOT NULL DEFAULT 0,
    model_outputs TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frame (
    scan_id INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    clip INTEGER NOT NULL,
    mapillary_image_id TEXT,
    sequence_id TEXT,
    lat REAL,
    lng REAL,
    compass REAL,
    captured_at TEXT,
    creator TEXT,
    width INTEGER,
    height INTEGER,
    PRIMARY KEY (scan_id, idx)
);
CREATE TABLE IF NOT EXISTS entrance (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    frame_idx INTEGER NOT NULL,
    place_name TEXT,
    category TEXT,
    side TEXT,
    lat REAL,
    lng REAL,
    location_source TEXT,
    step_count INTEGER,
    rise_cm_low REAL,
    rise_cm_high REAL,
    stairs_detected INTEGER,
    sill_possible INTEGER,
    has_ramp INTEGER,
    ramp_notes TEXT,
    door_type TEXT,
    door_width_cm_low REAL,
    door_width_cm_high REAL,
    has_handrail INTEGER,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'auto',
    review_reasons TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}',
    model_outputs TEXT NOT NULL DEFAULT '{}',
    corrections TEXT NOT NULL DEFAULT '[]',
    captured_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sidewalk_issue (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    issue_type TEXT NOT NULL,
    object_label TEXT,
    frame_start INTEGER,
    frame_end INTEGER,
    frame_idx INTEGER,
    lat REAL,
    lng REAL,
    overlap_ratio REAL,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'auto',
    review_reasons TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}',
    model_outputs TEXT NOT NULL DEFAULT '{}',
    corrections TEXT NOT NULL DEFAULT '[]',
    captured_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entrance_scan ON entrance(scan_id);
CREATE INDEX IF NOT EXISTS issue_scan ON sidewalk_issue(scan_id);
"""

JSON_COLUMNS = {"bbox", "sequence_ids", "stage_status", "review_reasons", "evidence", "model_outputs", "corrections"}
BOOL_COLUMNS = {"stairs_detected", "sill_possible", "has_ramp", "has_handrail", "audited"}
ROW_TABLES = {"frame", "entrance", "sidewalk_issue"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _encode(fields: dict) -> dict:
    out = {}
    for key, value in fields.items():
        if key in JSON_COLUMNS:
            out[key] = json.dumps(value, ensure_ascii=False)
        elif key in BOOL_COLUMNS and value is not None:
            out[key] = int(bool(value))
        else:
            out[key] = value
    return out


def _decode(row: sqlite3.Row) -> dict:
    out = dict(row)
    for key in out.keys() & JSON_COLUMNS:
        if out[key] is not None:
            out[key] = json.loads(out[key])
    for key in out.keys() & BOOL_COLUMNS:
        if out[key] is not None:
            out[key] = bool(out[key])
    return out


def _check_columns(conn, table: str, fields: dict) -> None:
    """Column names go into the SQL text, so only the table's real columns are allowed."""
    known = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    unknown = sorted(set(fields) - known)
    if unknown:
        raise ValueError(f"unknown {table} columns: {unknown}")


def _insert(conn, table: str, fields: dict) -> int:
    _check_columns(conn, table, fields)
    fields = _encode(fields)
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(fields.values()))
    conn.commit()
    return cur.lastrowid


def _update(conn, table: str, row_id: int, fields: dict) -> None:
    if not fields:
        return
    _check_columns(conn, table, fields)
    fields = _encode(fields)
    sets = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), row_id])
    conn.commit()


def create_scan(conn, source: str, bbox: list[float] | None, jurisdiction: str = "KR") -> int:
    return _insert(conn, "scan", {"created_at": now(), "source": source, "bbox": bbox, "jurisdiction": jurisdiction,
                                  "stage_status": {}, "sequence_ids": []})


def get_scan(conn, scan_id: int) -> dict:
    row = conn.execute("SELECT * FROM scan WHERE id = ?", (scan_id,)).fetchone()
    if row is None:
        raise KeyError(f"scan {scan_id} not found")
    return _decode(row)


def update_scan(conn, scan_id: int, **fields) -> None:
    _update(conn, "scan", scan_id, fields)


def add_review_seconds(conn, scan_id: int, seconds: float) -> None:
    """Add a reviewer's seconds in one statement, so two reviews at once both count."""
    conn.execute("UPDATE scan SET review_seconds = review_seconds + ? WHERE id = ?", (seconds, scan_id))
    conn.commit()


def set_stage(conn, scan_id: int, stage: str, status: str) -> None:
    stages = get_scan(conn, scan_id)["stage_status"]
    stages[stage] = status
    update_scan(conn, scan_id, stage_status=stages)


def public_cost(conn) -> float:
    """Spend of the scans visitors started on the server (they carry an address hash; CLI scans do not)."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM scan WHERE client_ip_hash IS NOT NULL").fetchone()
    return float(row[0])


def daily_cost(conn, day: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM scan WHERE created_at LIKE ?", (f"{day}%",)).fetchone()
    return float(row[0])


def add_frames(conn, scan_id: int, frames: list[dict]) -> None:
    with conn:  # commits on success, rolls back every row on any exception
        conn.executemany(
            "INSERT INTO frame (scan_id, idx, clip, mapillary_image_id, sequence_id, lat, lng, compass, captured_at, "
            "creator, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(scan_id, f["idx"], f["clip"], f["image_id"], f["sequence_id"], f["lat"], f["lng"], f["compass"],
              f["captured_at"], f["creator"], f["width"], f["height"]) for f in frames])


def frames(conn, scan_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM frame WHERE scan_id = ? ORDER BY idx", (scan_id,)).fetchall()
    out = []
    for row in rows:
        f = dict(row)
        f.pop("scan_id")
        f["image_id"] = f.pop("mapillary_image_id")
        out.append(f)
    return out


def add_entrance(conn, scan_id: int, fields: dict) -> int:
    return _insert(conn, "entrance", {"scan_id": scan_id, "created_at": now(), **fields})


def update_entrance(conn, entrance_id: int, **fields) -> None:
    _update(conn, "entrance", entrance_id, fields)


def entrances(conn, scan_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM entrance WHERE scan_id = ? ORDER BY id", (scan_id,)).fetchall()
    return [_decode(r) for r in rows]


def add_issue(conn, scan_id: int, fields: dict) -> int:
    return _insert(conn, "sidewalk_issue", {"scan_id": scan_id, "created_at": now(), **fields})


def update_issue(conn, issue_id: int, **fields) -> None:
    _update(conn, "sidewalk_issue", issue_id, fields)


def issues(conn, scan_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM sidewalk_issue WHERE scan_id = ? ORDER BY id", (scan_id,)).fetchall()
    return [_decode(r) for r in rows]


def clear_rows(conn, scan_id: int, table: str) -> None:
    """Delete one scan's rows in a stage table ("frame", "entrance" or "sidewalk_issue") before the stage runs
    again, so a resumed stage starts clean. Other scans are untouched."""
    if table not in ROW_TABLES:
        raise ValueError(f"not a stage table: {table}")
    conn.execute(f"DELETE FROM {table} WHERE scan_id = ?", (scan_id,))
    conn.commit()


def clear_issues(conn, scan_id: int, issue_types: tuple[str, ...]) -> None:
    """Delete one scan's sidewalk issues of the given types, and the picks of those types the model dropped, so the
    stage that makes them can run again without touching the other stage's rows (S4/S5 obstructions and S7 defects
    come from different stages)."""
    marks = ",".join("?" * len(issue_types))
    for table in ("sidewalk_issue", "dropped_pick"):
        conn.execute(f"DELETE FROM {table} WHERE scan_id = ? AND issue_type IN ({marks})", (scan_id, *issue_types))
    conn.commit()


def add_dropped(conn, scan_id: int, fields: dict) -> int:
    """A pick of a survey check the model said is not the target (survey_checks.run_check): no row on the map, but
    kept, so a report can say how many were dropped and a sample can be checked by a person (audited)."""
    return _insert(conn, "dropped_pick", {"scan_id": scan_id, "created_at": now(), **fields})


def dropped(conn, scan_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM dropped_pick WHERE scan_id = ? ORDER BY id", (scan_id,)).fetchall()
    return [_decode(r) for r in rows]


def get_entrance(conn, entrance_id: int) -> dict:
    row = conn.execute("SELECT * FROM entrance WHERE id = ?", (entrance_id,)).fetchone()
    if row is None:
        raise KeyError(f"entrance {entrance_id} not found")
    return _decode(row)


def get_issue(conn, issue_id: int) -> dict:
    row = conn.execute("SELECT * FROM sidewalk_issue WHERE id = ?", (issue_id,)).fetchone()
    if row is None:
        raise KeyError(f"sidewalk issue {issue_id} not found")
    return _decode(row)


def list_scans(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM scan ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_decode(r) for r in rows]


def scans_since(conn, client_ip_hash: str, since: str) -> int:
    """Scans started from this hashed address at or after since, a UTC time in the "%Y-%m-%dT%H:%M:%SZ" form that db.now() writes."""
    row = conn.execute("SELECT COUNT(*) FROM scan WHERE client_ip_hash = ? AND created_at >= ?",
                       (client_ip_hash, since)).fetchone()
    return int(row[0])
