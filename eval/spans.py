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
    """One-to-one matching of photo indexes within tol: as many pairs as possible, then the smallest total distance,
    so a prediction pairs with the entrance on its own photo rather than a neighbour (Myeongdong 172-173). On a line
    an optimal matching never crosses, so a DP over both sorted lists finds it."""
    ps = sorted(range(len(pred)), key=lambda i: pred[i])
    ts = sorted(range(len(truth)), key=lambda i: truth[i])
    best = [[(0, 0)] * (len(ts) + 1) for _ in range(len(ps) + 1)]  # (pairs, -total distance)
    move = [[""] * (len(ts) + 1) for _ in range(len(ps) + 1)]
    for i in range(1, len(ps) + 1):
        for j in range(1, len(ts) + 1):
            best[i][j], move[i][j] = best[i - 1][j], "p"
            if best[i][j - 1] > best[i][j]:
                best[i][j], move[i][j] = best[i][j - 1], "t"
            gap = abs(pred[ps[i - 1]] - truth[ts[j - 1]])
            if gap <= tol:
                paired = (best[i - 1][j - 1][0] + 1, best[i - 1][j - 1][1] - gap)
                if paired > best[i][j]:
                    best[i][j], move[i][j] = paired, "m"
    pairs, i, j = [], len(ps), len(ts)
    while i and j:
        if move[i][j] == "m":
            pairs.append((ps[i - 1], ts[j - 1]))
            i, j = i - 1, j - 1
        elif move[i][j] == "p":
            i -= 1
        else:
            j -= 1
    return sorted(pairs, key=lambda pair: pair[1])
