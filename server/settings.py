"""Server settings from environment variables (spec 13). Secrets stay in the environment and are never printed."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    run_name: str = "server"  # data folder: <THRESHOLD_DATA>/runs/<run_name>
    invite_code: str | None = field(default=None, repr=False)
    ip_hash_salt: str = field(default="threshold", repr=False)
    scans_per_ip_per_day: int = 3  # spec 13
    max_bbox_deg2: float = 0.0001  # about 1.1 x 0.9 km in Seoul; a scan stops at 400 photos anyway
    demo_scans: tuple[tuple[int, str], ...] = ()  # spec 13 demo mode: pre-processed areas shown first
    static_dir: Path = ROOT / "web" / "dist"


def _number(name: str, text: str, kind=int):
    try:
        return kind(text)
    except ValueError:
        raise ValueError(f"{name}: {text!r} is not a number") from None


def _demo_scans(text: str) -> tuple[tuple[int, str], ...]:
    """'3:Myeongdong;7:Seongsu' -> ((3, 'Myeongdong'), (7, 'Seongsu'))."""
    out = []
    for item in filter(None, (part.strip() for part in text.split(";"))):
        scan_id, _, name = item.partition(":")
        out.append((_number("DEMO_SCANS", scan_id), name.strip() or f"Scan {scan_id}"))
    return tuple(out)


def load() -> Settings:
    if os.environ.get("RAILWAY_ENVIRONMENT") and not os.environ.get("IP_HASH_SALT"):
        raise RuntimeError("IP_HASH_SALT must be set on Railway; the default salt is public")
    return Settings(
        run_name=os.environ.get("THRESHOLD_RUN") or "server",
        invite_code=os.environ.get("INVITE_CODE") or None,
        ip_hash_salt=os.environ.get("IP_HASH_SALT") or "threshold",
        scans_per_ip_per_day=_number("SCANS_PER_IP_PER_DAY", os.environ.get("SCANS_PER_IP_PER_DAY") or "3"),
        demo_scans=_demo_scans(os.environ.get("DEMO_SCANS") or ""),
    )
