"""Pure matching on photo indexes (plan 3): runs of labelled photos and one-to-one point matching."""


def runs(idxs) -> list[tuple[int, int]]:
    """Merge photo indexes into spans of consecutive photos."""
    out: list[tuple[int, int]] = []
    for i in sorted(set(idxs)):
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _overlaps(a: tuple[int, int], b: tuple[int, int], tol: int) -> bool:
    return a[0] - tol <= b[1] and b[0] - tol <= a[1]


def match_spans(pred: list[tuple[int, int]], truth: list[tuple[int, int]], tol: int = 1) -> dict:
    """Spot-level detection. A predicted span is a hit if it overlaps a truth span within tol photos; a truth span
    is found if any prediction overlaps it. Two predictions on one spot are both hits and find it once."""
    hit = [p for p in pred if any(_overlaps(p, t, tol) for t in truth)]
    found = [t for t in truth if any(_overlaps(p, t, tol) for p in pred)]
    return {"predicted": len(pred), "truth": len(truth), "hits": len(hit), "found": len(found),
            "false": [p for p in pred if p not in hit], "missed": [t for t in truth if t not in found]}


def match_points(pred: list[int], truth: list[int], tol: int) -> list[tuple[int, int]]:
    """Maximum one-to-one matching of photo indexes within tol. Each truth point, in order, takes the earliest
    unused prediction in its window; on a line with equal windows this greedy choice is optimal."""
    p_order = sorted(range(len(pred)), key=lambda i: pred[i])
    used: set[int] = set()
    pairs = []
    for ti in sorted(range(len(truth)), key=lambda i: truth[i]):
        for pi in p_order:
            if pi in used or pred[pi] < truth[ti] - tol:
                continue
            if pred[pi] <= truth[ti] + tol:
                pairs.append((pi, ti))
                used.add(pi)
            break
    return pairs
