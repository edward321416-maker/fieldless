"""Pre-flight check of spike/clips/truth.json, to run before any paid gate run.

  python -m spike.preflight [--truth PATH]

Reads the raw JSON, since load_truth checks the shape of the labels but not their values, and validates it against
the clips: every clip file exists; entrance t and obstacle t_start <= t_end are whole photo numbers inside the clip; step_count
is a whole number >= 0; door_type is one the step-count schema allows; tactile ranges have a <= b; obstacle labels
are not empty. Prints sample counts per clip and in total, and warns when the gate would rest on too few samples.
Exit code 1 on validation errors, 0 otherwise (warnings alone do not fail).
"""
import argparse
import json
import sys
from pathlib import Path

from spike import config, video
from spike.console import utf8_stdout
from spike.truth import load_truth, truth_path

DOOR_TYPES = {"swing", "sliding", "automatic", "revolving", "none_visible"}  # run_step_count.SCHEMA enum
ENTRANCE_KEYS = {"t", "name", "step_count", "has_ramp", "door_type"}
OBSTACLE_KEYS = {"t_start", "t_end", "label", "on_tactile"}
MIN_STEPPED = 10  # stepped entrances (step_count >= 1): the stair and step-count gates are scored on these
MIN_OBSTACLE_EVENTS = 10
MIN_TACTILE_PHOTOS = 30
COUNT_KEYS = ("photos", "entrances", "stepped", "steps_2plus", "tactile_photos", "obstacle_events",
              "labeling_seconds")


def _is_whole(v) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and v.is_integer())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _photo_problem(v, photos: int | None) -> str | None:
    """Why v is not a valid photo number in a clip of `photos` photos (None: length unknown), or None if it is."""
    if not _is_whole(v):
        return f"{v!r} is not a whole photo number"
    if photos is not None and not 0 <= v < photos:
        return f"{v:g} is outside the clip (photos 0..{photos - 1})"
    return None


def _clip_photos(path: Path) -> int:
    return round(video.probe(path).duration_s * config.CLIP_FPS)


def _check_entrances(entrances: list, photos: int | None, err, counts: dict) -> None:
    for i, e in enumerate(entrances):
        tag = f"entrance {i}"
        if not isinstance(e, dict):
            err(f"{tag}: not an object")
            continue
        counts["entrances"] += 1
        if missing := ENTRANCE_KEYS - e.keys():
            err(f"{tag}: missing {sorted(missing)}")
        if unknown := e.keys() - ENTRANCE_KEYS:
            err(f"{tag}: unknown keys {sorted(unknown)} (load_truth rejects them)")
        if "t" in e and (problem := _photo_problem(e["t"], photos)):
            err(f"{tag}: t {problem}")
        steps = e.get("step_count")
        if _is_whole(steps) and steps >= 0:
            counts["stepped"] += steps >= 1
            counts["steps_2plus"] += steps >= 2
        elif "step_count" in e:  # a missing key is reported above
            err(f"{tag}: step_count {steps!r} is not a whole number >= 0")
        if "door_type" in e and e["door_type"] not in DOOR_TYPES:
            err(f"{tag}: door_type {e['door_type']!r} is not one of {sorted(DOOR_TYPES)}")


def _check_tactile(ranges: list, photos: int | None, err, counts: dict) -> None:
    for i, pair in enumerate(ranges):
        tag = f"tactile range {i}"
        if not (isinstance(pair, list) and len(pair) == 2 and all(_is_number(v) for v in pair)):
            err(f"{tag}: {pair!r} is not a pair of numbers")
            continue
        a, b = pair
        for v in (a, b):
            if problem := _photo_problem(v, photos):
                err(f"{tag}: {problem}")
        if a > b:
            err(f"{tag}: start {a:g} is after end {b:g}")
        else:
            counts["tactile_photos"] += b - a + 1


def _check_obstacles(obstacles: list, photos: int | None, err, counts: dict) -> None:
    for i, o in enumerate(obstacles):
        tag = f"obstacle {i}"
        if not isinstance(o, dict):
            err(f"{tag}: not an object")
            continue
        counts["obstacle_events"] += 1
        if missing := OBSTACLE_KEYS - o.keys():
            err(f"{tag}: missing {sorted(missing)}")
        if unknown := o.keys() - OBSTACLE_KEYS:
            err(f"{tag}: unknown keys {sorted(unknown)} (load_truth rejects them)")
        for key in ("t_start", "t_end"):
            if key in o and (problem := _photo_problem(o[key], photos)):
                err(f"{tag}: {key} {problem}")
        if _is_number(o.get("t_start")) and _is_number(o.get("t_end")) and o["t_start"] > o["t_end"]:
            err(f"{tag}: t_start {o['t_start']:g} is after t_end {o['t_end']:g}")
        label = o.get("label")
        if not (isinstance(label, str) and label.strip()):
            err(f"{tag}: label {label!r} is empty")


def check_truth(raw, clips_dir: Path) -> tuple[list[str], dict]:
    """Validate parsed truth.json against the clips in clips_dir. Pure apart from probing the clip files.

    Returns (errors, counts); counts = {"clips": {file: {COUNT_KEYS...}}, "total": {COUNT_KEYS...}}, where photos is
    None for a clip that could not be probed.
    """
    errors: list[str] = []
    counts: dict = {"clips": {}, "total": dict.fromkeys(COUNT_KEYS, 0)}
    clips = raw.get("clips") if isinstance(raw, dict) else None
    if not isinstance(clips, dict) or not clips:
        return ["truth.json has no clips"], counts
    for name, clip in clips.items():
        def err(message: str, name=name) -> None:
            errors.append(f"{name}: {message}")

        if not isinstance(clip, dict):
            err("not an object")
            continue
        c = dict.fromkeys(COUNT_KEYS, 0)
        c["photos"] = None
        path = clips_dir / name
        if not path.exists():
            err(f"clip file not found: {path}")
        else:
            try:
                c["photos"] = _clip_photos(path)
            except Exception as exc:
                err(f"cannot read the clip length: {type(exc).__name__}: {exc}")
        for key, check in (("entrances", _check_entrances), ("tactile_ranges", _check_tactile),
                           ("obstacles", _check_obstacles)):
            items = clip.get(key, [])
            if isinstance(items, list):
                check(items, c["photos"], err, c)
            else:
                err(f"{key} is not a list")
        seconds = clip.get("labeling_seconds", 0)
        c["labeling_seconds"] = seconds if _is_whole(seconds) and seconds >= 0 else 0
        counts["clips"][name] = c
        for key in COUNT_KEYS:
            counts["total"][key] += c[key] or 0
    return errors, counts


def sample_warnings(total: dict) -> list[str]:
    warnings = []
    if total["stepped"] < MIN_STEPPED:
        warnings.append(f"WARNING: only {total['stepped']} stepped entrances (step_count >= 1); "
                        f"the stair and step-count gates want at least {MIN_STEPPED}")
    if total["obstacle_events"] < MIN_OBSTACLE_EVENTS:
        warnings.append(f"WARNING: only {total['obstacle_events']} obstacle events; "
                        f"the obstacle gate wants at least {MIN_OBSTACLE_EVENTS}")
    if total["tactile_photos"] < MIN_TACTILE_PHOTOS:
        warnings.append(f"WARNING: only {total['tactile_photos']:g} tactile photos; "
                        f"the tactile gate wants at least {MIN_TACTILE_PHOTOS}")
    return warnings


def format_counts(counts: dict) -> str:
    header = ("clip", "photos", "entrances", "stepped", "steps>=2", "tactile photos", "obstacle events",
              "labeling s")
    rows = [(name, *("?" if c[k] is None else f"{c[k]:g}" for k in COUNT_KEYS)) for name, c in counts["clips"].items()]
    rows.append(("TOTAL", *(f"{counts['total'][k]:g}" for k in COUNT_KEYS)))
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    return "\n".join("  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip() for r in [header, *rows])


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    parser = argparse.ArgumentParser(description="Validate spike/clips/truth.json before a paid run.")
    parser.add_argument("--truth", type=Path, default=None, help="truth file (default: spike/clips/truth.json)")
    args = parser.parse_args(argv)
    path = args.truth or truth_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read {path}: {type(exc).__name__}: {exc}")
        return 1
    errors, counts = check_truth(raw, config.CLIPS)
    if not errors:
        try:
            load_truth(path)  # the loader the run scripts use must accept it too
        except Exception as exc:
            errors.append(f"load_truth failed: {type(exc).__name__}: {exc}")
    print(format_counts(counts))
    for warning in sample_warnings(counts["total"]):
        print(warning)
    for error in errors:
        print(f"ERROR: {error}")
    print(f"{len(errors)} validation error(s) in {path}" if errors else f"OK: {path} is valid")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
