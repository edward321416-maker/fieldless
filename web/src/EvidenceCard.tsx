import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, STATIC_DEMO } from "./api";
import { photoMonth, reasonText, zoomChip } from "./format";
import { compassWord, targetBearing } from "./geo";
import { colourOf, STATUS_SENTENCE, type Finding } from "./items";
import { itemArt } from "./markerArt";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import { choiceForKey, choicesFor, type Choice } from "./review";
import type { CheckCount, CityCheck, ItemMeta, Picked } from "./types";

export { MapillaryMark, PhotoCredit } from "./Photo";

interface Props {
  finding: Finding;
  list: Finding[]; // what the arrows step through: the item's findings, or the review queue
  queue: boolean; // reviewing: after an answer, go to the next finding waiting for a person
  items: ItemMeta[];
  checks: CheckCount[]; // the scan's checks, for their short questions
  photoOnMap: boolean; // the photo window shows the big photo: the card keeps a thumbnail
  backLabel: string; // where the back button goes: "← Street tree (31)", "← Stop reviewing"
  progress?: { done: number; total: number }; // a review's place (Project Sidewalk's Validate progress bar)
  street: string | undefined;
  city: CityCheck | undefined;
  onNavigate: (f: Finding) => void;
  onClose: () => void;
  onUpdated: (p: Picked) => void;
}

const UNDO_SHOWN_MS = 8000; // the server takes a decision back for 60 seconds (server/review.py UNDO_WINDOW_S)

function aside(heading: number | null, bearing: number | null): string {
  if (heading === null || bearing === null) return "";
  const d = ((bearing - heading + 540) % 360) - 180;
  return Math.abs(d) < 10 ? "straight ahead" : d < 0 ? "ahead, to the left" : "ahead, to the right";
}

// The evidence card, 3rd version (docs/specs/2026-10-28-ui-redesign-design.md 3.4, docs/ui-rules.md U5): what it
// is and its status, the photo with the model's box, where it is, what the model was asked and answered, reference
// chips, and the person's answer with keys and Undo.
export default function EvidenceCard(props: Props) {
  const { finding: f, items } = props;
  const p = f.picked;
  const art = useMemo(() => itemArt(items), [items]);
  const target = items.find((it) => it.id === f.item)?.target ?? f.item.replace(/_/g, " ");
  const index = props.list.findIndex((x) => x.key === f.key);
  const shownAt = useRef(performance.now());
  const hiddenMs = useRef(0);
  const hiddenAt = useRef<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ previous: Finding } | null>(null);
  const [steps, setSteps] = useState(0);
  const { question, choices } = choicesFor(p);
  // an agent item's rows belong to its run, which has no review (docs/specs/2026-10-29-agent-items-design.md 2)
  const reviewable = !STATIC_DEMO && ["needs_review", "auto"].includes(f.status) && !(p.kind === "issue" && p.item.item_run);

  useEffect(() => {
    shownAt.current = performance.now();
    hiddenMs.current = 0;
    hiddenAt.current = document.hidden ? performance.now() : null;
    setError(null);
    if (p.kind === "entrance") setSteps(p.item.step_count ?? 0);
  }, [f.key]);

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

  function step(by: number) {
    const next = props.list[index + by];
    if (next) props.onNavigate(next);
  }

  function nextAfterAnswer(): Finding | undefined {
    const after = [...props.list.slice(index + 1), ...props.list.slice(0, index)];
    return props.queue ? after.find((x) => x.status === "needs_review") : props.list[index + 1];
  }

  async function answer(choice: { send: Choice["send"] }) {
    if (busy || !reviewable) return;
    const hidden = hiddenMs.current + (hiddenAt.current !== null ? performance.now() - hiddenAt.current : 0);
    const seconds = Math.min(3600, Math.round((performance.now() - shownAt.current - hidden) / 100) / 10);
    setBusy(true);
    setError(null);
    try {
      const updated: Picked = choice.send.kind === "issue"
        ? { kind: "issue", item: await api.reviewIssue(p.item.id, { ...choice.send.body, seconds }) }
        : { kind: "entrance", item: await api.reviewEntrance(p.item.id, { ...choice.send.body, seconds }) };
      const next = nextAfterAnswer();
      setSaved({ previous: f });
      props.onUpdated(updated);
      if (next) props.onNavigate(next);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "Someone already answered this one." : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!saved) return;
    const prev = saved.previous.picked;
    setSaved(null);
    try {
      const restored: Picked = prev.kind === "issue"
        ? { kind: "issue", item: await api.undoIssue(prev.item.id) }
        : { kind: "entrance", item: await api.undoEntrance(prev.item.id) };
      props.onUpdated(restored);
      props.onNavigate({ ...saved.previous, status: restored.item.review_status, picked: restored });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // Keys: the answers' letters, arrows to step, Z to undo, Escape to close; not while typing in a field.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null;
      if (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
      if (ev.key === "ArrowRight") step(1);
      else if (ev.key === "ArrowLeft") step(-1);
      else if (ev.key === "Escape") props.onClose();
      else if (ev.key.toLowerCase() === "z" && saved) undo();
      else {
        const c = reviewable ? choiceForKey(choices, ev.key) : undefined;
        if (!c) return;
        answer(c);
      }
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (!saved) return;
    const t = window.setTimeout(() => setSaved(null), UNDO_SHOWN_MS);
    return () => window.clearTimeout(t);
  }, [saved]);

  const bearing = targetBearing(f.heading, f.box);
  const facing = f.heading !== null ? `facing ${compassWord(f.heading)}` : "";
  const where = aside(f.heading, bearing);
  const check = p.kind === "issue" ? p.item.check : null;
  const match = p.kind === "issue" && typeof p.item.id === "number"
    && props.city?.items[f.item]?.matched_ids.includes(p.item.id);
  const evidence = p.item.evidence;
  const title = p.kind === "entrance" ? `${p.item.name || "Unnamed entrance"}: ${p.item.label.text}` : p.item.label.text;

  // The person's answer: in a review it comes first, right under what the finding is (docs/specs/2026-10-29-cleanup-design.md 4).
  const answers = reviewable && (
    <div className={`answers${props.queue ? " answers-first" : ""}`}>
      <h3>{question}{f.status === "auto" && <span className="hint small"> (optional: the models were sure)</span>}</h3>
      {choices.map((c) => (
        <button key={c.key} type="button" className={c.main ? "answer primary" : "answer"} disabled={busy}
          onClick={() => answer(c)}>
          <kbd>{c.key}</kbd> {c.label}
        </button>
      ))}
      {p.kind === "entrance" && !p.item.review_reasons.includes("possible_sill") && (
        <div className="row">
          <label className="field inline">
            Steps
            <input type="number" min={0} max={50} value={steps}
              onChange={(e) => setSteps(Math.max(0, Math.min(50, Math.round(Number(e.target.value) || 0))))} />
          </label>
          <button type="button" disabled={busy}
            onClick={() => answer({ send: { kind: "entrance", body: { action: "correct", step_count: steps } } })}>
            Save the step count
          </button>
        </div>
      )}
    </div>
  );
  const progress = props.progress;

  return (
    <section className="card evidence-card" aria-labelledby="card-title">
      {progress && progress.total > 0 && (
        <div className="review-progress">
          <p><strong>Review what the models were unsure of</strong>
            <span>{Math.min(progress.done + 1, progress.total)} of {progress.total}</span></p>
          <span className="bar" aria-hidden="true"><span style={{ width: `${(progress.done / progress.total) * 100}%` }} /></span>
        </div>
      )}
      <div className="card-nav">
        <button type="button" onClick={props.onClose}>{props.backLabel}</button>
        <span className="hint">{!props.queue && index >= 0 ? `${index + 1} of ${props.list.length}` : ""}</span>
        <span className="card-arrows">
          <button type="button" onClick={() => step(-1)} disabled={index <= 0} aria-label="Previous (left arrow)">‹</button>
          <button type="button" onClick={() => step(1)} disabled={index < 0 || index >= props.list.length - 1}
            aria-label="Next (right arrow)">›</button>
        </span>
      </div>
      {saved && (
        <p className="saved" role="status">
          Saved · <button type="button" className="link-button inline" onClick={undo}>Undo (Z)</button>
        </p>
      )}
      <h2 id="card-title" className="card-title">
        <img src={art[f.item]} alt="" width={36} height={36} /> {title}
      </h2>
      <p className="status-line">{STATUS_SENTENCE[f.status] ?? f.status}</p>
      {props.queue && error && <p className="error" role="alert">{error}</p>}
      {props.queue && answers}

      {evidence && props.photoOnMap ? (
        <p className="card-thumb">
          <img src={evidence.url} alt="" />
          <span className="hint small">The photo is open over the map.</span>
        </p>
      ) : evidence ? (
        <>
          <EvidencePhoto evidence={evidence} box={f.box} boxLabel={`the ${target}`} colour={colourOf(f.item, items)}
            alt={`Street photo: ${title}`} />
          <PhotoCredit evidence={evidence} />
        </>
      ) : (
        <p className="hint">No evidence photo for this item.</p>
      )}

      <h3>Where</h3>
      <p>
        {props.street ?? "Street name not on the map"}
        {facing && <> · photo taken {facing}</>}
        {where && <>; the {target} is {where}, position approximate</>}.
      </p>

      {check ? (
        <>
          <h3>We asked {check.model}</h3>
          <p className="asked">{props.checks.find((c) => c.id === check.id)?.question ?? check.question}</p>
          <details className="instruction">
            <summary>Full instruction to the model</summary>
            <p>{check.question}</p>
          </details>
          <p><strong>Answer: {p.kind === "issue" ? p.item.label.text : ""}</strong>
            {check.note && <> · “{check.note}”</>}</p>
          {p.kind === "issue" && p.item.defect && (
            <p className="hint">{f.status === "verified" ? "→ Confirmed as a defect by a person."
              : `→ Listed as a possible defect for you to confirm. Look at the boxed ${target}.`}</p>
          )}
        </>
      ) : p.kind === "entrance" ? (
        <dl className="stats">
          <dt>Steps</dt><dd>{p.item.label.text}</dd>
          <dt>Stairs outlined by SAM 3.1</dt>
          <dd>{p.item.stairs_detected === null ? "not checked" : p.item.stairs_detected ? "yes" : "no"}</dd>
        </dl>
      ) : (
        <p>{p.item.durable ? "A defect of the paving itself, so it is likely still there today."
          : `Seen in the photo of ${photoMonth(p.item.captured_at)}; movable objects may be gone now.`}</p>
      )}

      {(match || p.item.old_photo || (p.kind === "issue" && p.item.second_opinion) || p.item.review_reasons.length > 0
        || zoomChip(check)) && (
        <p className="chips">
          {zoomChip(check) && <span className="chip">{zoomChip(check)}</span>}
          {match && <span className="chip">{props.city!.city}'s list has a {target} here</span>}
          {p.item.old_photo && <span className="chip">Photo over 3 years old ({photoMonth(p.item.captured_at)})</span>}
          {p.kind === "issue" && p.item.second_opinion && (
            <span className="chip">{p.item.second_opinion.model} {p.item.second_opinion.agree ? "agrees" : "answered differently"}</span>
          )}
          {p.item.review_reasons.map((r) => <span key={r} className="chip">{reasonText(r)}</span>)}
        </p>
      )}

      {!props.queue && error && <p className="error" role="alert">{error}</p>}
      {!props.queue && answers}
    </section>
  );
}
