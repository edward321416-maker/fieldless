"""Server settings from environment variables (spec 13). Secrets stay in the environment and are never printed."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    run_name: str = "server"  # data folder: <THRESHOLD_DATA>/runs/<run_name>
    invite_code: str | None = None
    ip_hash_salt: str = "threshold"
    scans_per_ip_per_day: int = 3  # spec 13
    max_bbox_deg2: float = 0.0001  # about 1.1 x 0.9 km in Seoul; a scan stops at 400 photos anyway
    demo_scans: tuple[tuple[int, str], ...] = ()  # spec 13 demo mode: pre-processed areas shown first
    static_dir: Path = ROOT / "web" / "dist"


def _demo_scans(text: str) -> tuple[tuple[int, str], ...]:
    """'3:Myeongdong;7:Seongsu' -> ((3, 'Myeongdong'), (7, 'Seongsu'))."""
    out = []
    for item in filter(None, (part.strip() for part in text.split(";"))):
        scan_id, _, name = item.partition(":")
        out.append((int(scan_id), name.strip() or f"Scan {scan_id}"))
    return tuple(out)


def load() -> Settings:
    return Settings(
        run_name=os.environ.get("THRESHOLD_RUN") or "server",
        invite_code=os.environ.get("INVITE_CODE") or None,
        ip_hash_salt=os.environ.get("IP_HASH_SALT") or "threshold",
        scans_per_ip_per_day=int(os.environ.get("SCANS_PER_IP_PER_DAY") or 3),
        demo_scans=_demo_scans(os.environ.get("DEMO_SCANS") or ""),
    )
