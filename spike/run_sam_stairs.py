"""Gate test 2: does SAM 3.1 segment entrance stairs, and which concept phrase works best?"""
import json
from pathlib import Path

from spike import config, video
from spike import meta_client as mc
from spike.sam_parse import confident, parse_sam_output
from spike.truth import load_truth

PHRASES = ["stairs", "steps", "entrance steps", "staircase"]
WINDOW_S = 2.0
FPS = config.CLIP_FPS
CONF = 0.5


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def run_entrance(clip_file: str, index: int, entrance, src: Path, stem: str, tmp: Path, raw_dir: Path) -> dict:
    """One entrance window. A failure in this window or in one phrase is recorded on the row, never raised."""
    start = max(0.0, entrance.t - WINDOW_S)
    row = {"clip": clip_file, "index": index, "t": entrance.t, "window_start": start, "phrases": {}}
    try:
        window = video.cut(src, tmp / f"{stem}_e{index}.mp4", start, entrance.t + WINDOW_S + 1.0 / FPS)
        billed = max(50, round(video.probe(window).duration_s * FPS))
        file_id = mc.upload(window)
    except Exception as exc:
        row["error"] = _err(exc)
        return row
    try:
        for phrase in PHRASES:
            try:
                text = mc.sam(phrase, mc.video_part(file_id), billed_frames=billed)
                (raw_dir / f"{stem}_e{index}_{phrase.replace(' ', '_')}.txt").write_text(text, encoding="utf-8")
                dets = confident(parse_sam_output(text), CONF)
                best = max(dets, key=lambda d: d.area_ratio, default=None)
                row["phrases"][phrase] = {
                    "detected": bool(dets),
                    "max_c": max((d.c if d.c is not None else 1.0 for d in dets), default=0.0),
                    "best_frame_t": start + best.frame / FPS if best else None,
                }
            except Exception as exc:
                row["phrases"][phrase] = {"error": _err(exc)}
    finally:
        mc.delete_file(file_id)
    return row


def summarize(rows: list[dict]) -> tuple[dict[str, float], str | None]:
    """Detection rate per phrase over requests that succeeded. A phrase with no successful request has no rate."""
    rates: dict[str, float] = {}
    for phrase in PHRASES:
        scored = [r["phrases"][phrase] for r in rows if phrase in r["phrases"] and "error" not in r["phrases"][phrase]]
        if scored:
            rates[phrase] = sum(s["detected"] for s in scored) / len(scored)
    best_phrase = max(rates, key=rates.get) if rates else None
    return rates, best_phrase


def main() -> None:
    tmp = config.OUT / "tmp"
    raw_dir = config.OUT / "sam_stairs"
    tmp.mkdir(exist_ok=True)
    raw_dir.mkdir(exist_ok=True)
    out_path = config.OUT / "sam_stairs.json"
    rows: list[dict] = []
    for clip in load_truth():
        src = config.CLIPS / clip.file
        stem = Path(clip.file).stem
        for i, entrance in enumerate(clip.entrances):
            row = run_entrance(clip.file, i, entrance, src, stem, tmp, raw_dir)
            rows.append(row)
            shown = {p: v.get("detected", v.get("error")) for p, v in row["phrases"].items()}
            print(entrance.name, shown, row.get("error", ""))
        rates, best_phrase = summarize(rows)
        out_path.write_text(
            json.dumps({"rows": rows, "rates": rates, "best_phrase": best_phrase}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    rates, best_phrase = summarize(rows)
    print("rates:", rates, "best:", best_phrase)


if __name__ == "__main__":
    main()
