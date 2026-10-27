"""Score Spark's tactile-paving condition answers against hand labels (spike: durable sidewalk defects)."""

STRUCTURAL_DAMAGE = {"broken", "missing"}
FLAGGED_DAMAGE = STRUCTURAL_DAMAGE | {"worn", "covered"}


def is_structural(ans: dict) -> bool:
    """An abrupt line end, or broken or missing blocks."""
    return ans.get("line_end") == "abrupt" or ans.get("damage") in STRUCTURAL_DAMAGE


def is_flagged(ans: dict) -> bool:
    """Any reported problem, including fading and covering."""
    return is_structural(ans) or ans.get("damage") in FLAGGED_DAMAGE


def score(rows: dict[int, dict], labels: dict) -> dict:
    """Hits on structural and worn labels, false flags on intact photos. Rows with "error" never count as hits."""
    structural = sorted(int(k) for k in labels["structural"])
    worn = sorted(int(k) for k in labels["worn"])
    intact = sorted(int(k) for k in labels["intact"])
    ok = {i: a for i, a in rows.items() if "error" not in a}
    return {
        "structural_hit": sum(1 for i in structural if i in ok and is_structural(ok[i])),
        "structural_total": len(structural),
        "worn_hit": sum(1 for i in worn if i in ok and ok[i].get("damage") == "worn"),
        "worn_total": len(worn),
        "intact_flagged": sum(1 for i in intact if i in ok and is_flagged(ok[i])),
        "intact_total": len(intact),
        "errors": sorted(i for i, a in rows.items() if "error" in a),
        "missing": sorted(i for i in structural + worn + intact if i not in rows),
    }
