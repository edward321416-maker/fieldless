import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
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

const keyOf = (p: Picked) => `${p.kind}:${p.item.id}`;

export default function ReviewPanel({ entrances, issues, first = null, onUpdated, onClose }: Props) {
  // Items someone else reviewed meanwhile (the server answered 409); they leave the queue.
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  // The item the surveyor picked comes first; then sidewalk items (durable first, server order), then entrances.
  const queue = useMemo<Picked[]>(() => {
    const all: Picked[] = [
      ...issues.filter((i) => i.review_status === "needs_review").map((item) => ({ kind: "issue" as const, item })),
      ...entrances.filter((e) => e.review_status === "needs_review").map((item) => ({ kind: "entrance" as const, item })),
    ].filter((q) => !skipped.has(keyOf(q)));
    const head = first ? all.find((q) => q.kind === first.kind && q.item.id === first.item.id) : undefined;
    return head ? [head, ...all.filter((q) => q !== head)] : all;
  }, [entrances, issues, first, skipped]);
  const current = queue[0] ?? null;
  const shownAt = useRef(performance.now());
  const hiddenMs = useRef(0);
  const hiddenAt = useRef<number | null>(null);
  const [steps, setSteps] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [times, setTimes] = useState<number[]>([]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) hiddenAt.current = performance.now();
      else if (hiddenAt.current !== null) {
        hiddenMs.current += performance.now() - hiddenAt.current;
        hiddenAt.current = null;
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    shownAt.current = performance.now();
    hiddenMs.current = 0;
    hiddenAt.current = document.hidden ? performance.now() : null;
    setError(null);
    if (current?.kind === "entrance") setSteps(current.item.step_count ?? 0);
  }, [current?.kind, current?.item.id]);

  async function decide(send: (seconds: number) => Promise<Picked>) {
    const hidden = hiddenMs.current + (hiddenAt.current !== null ? performance.now() - hiddenAt.current : 0);
    const seconds = Math.min(3600, Math.round((performance.now() - shownAt.current - hidden) / 100) / 10);
    setBusy(true);
    setNotice(null);
    try {
      const updated = await send(seconds);
      setTimes((t) => [...t, seconds]);
      onUpdated(updated);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && current) {
        setSkipped((s) => new Set(s).add(keyOf(current)));
        setNotice("That item was already reviewed, so it was skipped.");
      } else {
        setError((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  // A possible sill gets a yes or no answer: the model's own step count is a confirm, any other answer a correction.
  const answerSteps = (entrance: Entrance, n: number) =>
    decide(async (seconds) => ({
      kind: "entrance",
      item: await api.reviewEntrance(entrance.id, n === entrance.step_count
        ? { action: "confirm", seconds }
        : { action: "correct", seconds, step_count: n }),
    }));

  // A check row the model could not answer: the surveyor answers it, and the answer decides whether it is a defect.
  const answerCheck = (issue: Issue, answer: "yes" | "no") =>
    decide(async (seconds) => ({ kind: "issue", item: await api.reviewIssue(issue.id, { action: "answer", answer, seconds }) }));

  const done = times.length > 0 && (
    <p role="status">Reviewed {times.length} item{times.length === 1 ? "" : "s"}, median {median(times).toFixed(1)} s each.</p>
  );

  if (!current) {
    return (
      <section className="card" aria-labelledby="review-title">
        <h2 id="review-title">Review</h2>
        <p>Nothing left to review.</p>
        {notice && <p className="hint" role="status">{notice}</p>}
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
      {notice && <p className="hint" role="status">{notice}</p>}
      <h3>{current.kind === "entrance" ? current.item.name || "Unnamed entrance" : labelText(current.item.label)}</h3>
      {item.evidence ? (
        <>
          <img src={item.evidence.url} alt={`Street photo to check: ${current.kind === "entrance" ? `${current.item.name || "Entrance"}, ${current.item.label.text}` : current.item.label.text}`} className="evidence" />
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
          {current.item.review_reasons.includes("possible_sill") ? (
            <div className="row">
              <button type="button" className="primary" disabled={busy} onClick={() => answerSteps(current.item, 1)}>Yes, one step</button>
              <button type="button" disabled={busy} onClick={() => answerSteps(current.item, 0)}>No step</button>
            </div>
          ) : (
            <div className="row">
              <button type="button" className="primary" disabled={busy}
                onClick={() => decide(async (seconds) => ({ kind: "entrance", item: await api.reviewEntrance(current.item.id, { action: "confirm", seconds }) }))}>
                Correct as shown
              </button>
            </div>
          )}
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
          <p>{issueQuestion(current.item)}</p>
          {current.item.check && current.item.check.answer === "unclear" ? (
            <div className="row">
              <button type="button" className="primary" disabled={busy} onClick={() => answerCheck(current.item, "yes")}>
                Yes: {current.item.check.label_yes}
              </button>
              <button type="button" disabled={busy} onClick={() => answerCheck(current.item, "no")}>
                No: {current.item.check.label_no}
              </button>
            </div>
          ) : (
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
          )}
        </>
      )}
    </section>
  );
}
