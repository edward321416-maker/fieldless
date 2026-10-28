"""Server settings from environment variables (spec 13). Secrets stay in the environment and are never printed."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Any one of these means a hosted server (Railway, Hugging Face Spaces, Modal), where the public default salt must not
# be used.
HOSTED_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "SPACE_ID", "MODAL_TASK_ID",
                  "MODAL_IS_REMOTE")


@dataclass(frozen=True)
class Settings:
    run_name: str = "server"  # data folder: <THRESHOLD_DATA>/runs/<run_name>
    invite_code: str | None = field(default=None, repr=False)
    ip_hash_salt: str = field(default="threshold", repr=False)
    scans_per_ip_per_day: int = 3  # spec 13; 0 turns the per-address limit off
    # Total spend of the scans visitors start; past it the live site stops new scans (finished ones stay open). It
    # keeps a public server from running the free credit out and onto the card. None: no such cap.
    public_cost_cap_usd: float | None = None
    max_bbox_deg2: float = 0.0001  # about 1.1 x 0.9 km in Seoul; a scan stops at 400 photos anyway
    demo_scans: tuple[tuple[int, str], ...] = ()  # spec 13 demo mode: pre-processed areas shown first
    hf_data_repo: str | None = None  # private Hugging Face dataset that keeps the data on a host without a disk
    backup_dir: Path | None = None  # or a backup folder (a mounted Modal Volume) that keeps it
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
    if any(os.environ.get(name) for name in HOSTED_MARKERS) and not os.environ.get("IP_HASH_SALT"):
        raise RuntimeError("IP_HASH_SALT must be set on a hosted server; the default salt is public")
    return Settings(
        run_name=os.environ.get("THRESHOLD_RUN") or "server",
        invite_code=os.environ.get("INVITE_CODE") or None,
        ip_hash_salt=os.environ.get("IP_HASH_SALT") or "threshold",
        scans_per_ip_per_day=_number("SCANS_PER_IP_PER_DAY", os.environ.get("SCANS_PER_IP_PER_DAY") or "3"),
        public_cost_cap_usd=(_number("PUBLIC_COST_CAP_USD", os.environ["PUBLIC_COST_CAP_USD"], float)
                             if os.environ.get("PUBLIC_COST_CAP_USD") else None),
        demo_scans=_demo_scans(os.environ.get("DEMO_SCANS") or ""),
        hf_data_repo=os.environ.get("HF_DATA_REPO") or None,
        backup_dir=Path(os.environ["BACKUP_DIR"]) if os.environ.get("BACKUP_DIR") else None,
    )
