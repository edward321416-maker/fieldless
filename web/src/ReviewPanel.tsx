import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { PhotoCredit } from "./EvidenceCard";
import { issueQuestion, labelText, median, reasonText } from "./format";
import type { Picked } from "./MapView";
import type { Entrance, Issue } from "./types";

interface Props {
  entrances: Entrance[];
  issues: Issue[];
  first?: Picked | null;
  onUpdated: (updated: Picked) => void;
  onClose: () => void;
}

export default function ReviewPanel({ entrances, issues, first = null, onUpdated, onClose }: Props) {
  // The item the surveyor picked comes first; then sidewalk items (durable first, server order), then entrances.
  const queue = useMemo<Picked[]>(() => {
    const all: Picked[] = [
      ...issues.filter((i) => i.review_status === "needs_review").map((item) => ({ kind: "issue" as const, item })),
      ...entrances.filter((e) => e.review_status === "needs_review").map((item) => ({ kind: "entrance" as const, item })),
    ];
    const head = first ? all.find((q) => q.kind === first.kind && q.item.id === first.item.id) : undefined;
    return head ? [head, ...all.filter((q) => q !== head)] : all;
  }, [entrances, issues, first]);
  const current = queue[0] ?? null;
  const shownAt = useRef(performance.now());
  const [steps, setSteps] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [times, setTimes] = useState<number[]>([]);

  useEffect(() => {
    shownAt.current = performance.now();
    setError(null);
    if (current?.kind === "entrance") setSteps(current.item.step_count ?? 0);
  }, [current?.kind, current?.item.id]);

  async function decide(send: (seconds: number) => Promise<Picked>) {
    const seconds = Math.round((performance.now() - shownAt.current) / 100) / 10;
    setBusy(true);
    try {
      const updated = await send(seconds);
      setTimes((t) => [...t, seconds]);
      onUpdated(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const done = times.length > 0 && (
    <p role="status">Reviewed {times.length} item{times.length === 1 ? "" : "s"}, median {median(times).toFixed(1)} s each.</p>
  );

  if (!current) {
    return (
      <section className="card" aria-labelledby="review-title">
        <h2 id="review-title">Review</h2>
        <p>Nothing left to review.</p>
        {done}
        <button type="button" onClick={onClose}>Back to results</button>
      </section>
    );
  }

  const item = current.item;
  return (
    <section className="card" aria-labelledby="review-title">
      <div className="card-head">
        <h2 id="review-title">Review {queue.length} left</h2>
        <button type="button" onClick={onClose} aria-label="Close review">×</button>
      </div>
      {done}
      <h3>{current.kind === "entrance" ? current.item.name || "Unnamed entrance" : labelText(current.item.label)}</h3>
      {item.evidence ? (
        <>
          <img src={item.evidence.url} alt="Street photo to check" className="evidence" />
          <PhotoCredit evidence={item.evidence} />
        </>
      ) : (
        <p className="hint">No evidence photo for this item.</p>
      )}
      <ul>
        {item.review_reasons.map((r) => <li key={r}>{reasonText(r)}</li>)}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
      {current.kind === "entrance" ? (
        <>
          <p>Model: {labelText(current.item.label)}</p>
          <div className="row">
            <button type="button" className="primary" disabled={busy}
              onClick={() => decide(async (seconds) => ({ kind: "entrance", item: await api.reviewEntrance(current.item.id, { action: "confirm", seconds }) }))}>
              Correct as shown
            </button>
          </div>
          <fieldset>
            <legend>Or fix the step count</legend>
            <label className="field">
              Steps
              <input type="number" min={0} max={50} value={steps} onChange={(e) => setSteps(Math.max(0, Math.min(50, Math.round(Number(e.target.value) || 0))))} />
            </label>
            <button type="button" disabled={busy}
              onClick={() => decide(async (seconds) => ({
                kind: "entrance",
                item: await api.reviewEntrance(current.item.id, { action: "correct", seconds, step_count: steps }),
              }))}>
              Save fix
            </button>
          </fieldset>
        </>
      ) : (
        <>
          <p>{issueQuestion(current.item.issue_type)}</p>
          <div className="row">
            <button type="button" className="primary" disabled={busy}
              onClick={() => decide(async (seconds) => ({ kind: "issue", item: await api.reviewIssue(current.item.id, { action: "confirm", seconds }) }))}>
              Yes
            </button>
            <button type="button" disabled={busy}
              onClick={() => decide(async (seconds) => ({ kind: "issue", item: await api.reviewIssue(current.item.id, { action: "reject", seconds }) }))}>
              No
            </button>
          </div>
        </>
      )}
    </section>
  );
}
