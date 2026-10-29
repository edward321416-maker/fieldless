"""Put some surveys of a local run into the live database without replacing the rest of it.

A full seed (deploy/modal_app.py --seed) overwrites the live database, and with it the surveys and item runs visitors
made there. merge_scans copies only the named surveys: their scan row, photos (frame), entrances, findings
(sidewalk_issue), dropped picks and report text replace what the live database holds for them. The item runs visitors
made on them stay. Rows get the live database's next ids, since the two databases number their rows apart; a row's
evidence photo keeps its file name, which the row names. What else names a finding by id, the offline comparison
with the city's records (city_check_scan<N>.json), is rewritten with remap_city_check.
"""
import sqlite3
from pathlib import Path

from pipeline import db

CHILDREN = ("frame", "entrance", "sidewalk_issue", "dropped_pick", "report_text")
KEEPS_ID = {"frame", "report_text"}  # keyed by (scan, photo idx) and by scan; no row id of their own


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def merge_scans(src_path: Path, dst_path: Path, scan_ids: list[int]) -> dict[int, dict]:
    """Copy the scans from src into dst in one transaction. Returns, per scan, the rows written per table ("counts")
    and each copied row's new id by its old one ("ids": {table: {old: new}})."""
    src = sqlite3.connect(f"file:{Path(src_path).as_posix()}?mode=ro", uri=True)
    dst = db.connect(dst_path)  # brings an older live database up to the current schema
    written: dict[int, dict] = {}
    try:
        with dst:
            for scan_id in scan_ids:
                cols = _columns(src, "scan")
                assert cols[0] == "id"
                row = src.execute(f"SELECT {', '.join(cols)} FROM scan WHERE id = ?", (scan_id,)).fetchone()
                if row is None:
                    raise KeyError(f"scan {scan_id} is not in {src_path}")
                if dst.execute("SELECT 1 FROM scan WHERE id = ?", (scan_id,)).fetchone():
                    dst.execute(f"UPDATE scan SET {', '.join(f'{c} = ?' for c in cols[1:])} WHERE id = ?",
                                (*row[1:], scan_id))
                else:
                    dst.execute(f"INSERT INTO scan ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", row)
                counts, ids = {}, {}
                for table in CHILDREN:
                    cols = [c for c in _columns(src, table) if table in KEEPS_ID or c != "id"]
                    picked = cols if table in KEEPS_ID else ["id", *cols]
                    rows = src.execute(f"SELECT {', '.join(picked)} FROM {table} WHERE scan_id = ? ORDER BY rowid",
                                       (scan_id,)).fetchall()
                    dst.execute(f"DELETE FROM {table} WHERE scan_id = ?", (scan_id,))
                    insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
                    if table in KEEPS_ID:
                        dst.executemany(insert, rows)
                    else:
                        ids[table] = {r[0]: dst.execute(insert, r[1:]).lastrowid for r in rows}
                    counts[table] = len(rows)
                written[scan_id] = {"counts": counts, "ids": ids}
    finally:
        src.close()
        dst.close()
    return written


def remap_city_check(check: dict, issue_ids: dict[int, int]) -> dict:
    """The city-record comparison of one survey (eval/city_check.py) with its findings' ids taken through issue_ids
    (old id -> new). Raises KeyError for an id the survey does not have."""
    items = {}
    for name, item in check.get("items", {}).items():
        items[name] = {**item,
                       "matched_ids": sorted(issue_ids[i] for i in item.get("matched_ids") or []),
                       "rejected_at_city_point": sorted(issue_ids[i] for i in item.get("rejected_at_city_point") or []),
                       "pairs": [{**p, "issue_id": issue_ids[p["issue_id"]]} for p in item.get("pairs") or []]}
    return {**check, "items": items}
