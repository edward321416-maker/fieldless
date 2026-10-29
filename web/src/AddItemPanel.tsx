import { useEffect, useState } from "react";
import { dollars, photoMonth } from "./format";
import { capital } from "./items";
import type { ItemRun, ItemRunSummary } from "./types";

type Answer = "yes" | "no" | "unclear";

interface Props {
  canMake: boolean; // the server makes items (an invite code is set) and this is not the static demo
  run: ItemRun | null; // the run on screen, for this survey
  examples: ItemRunSummary[]; // the survey's finished runs
  defaultOpen?: boolean;
  onStart: (target: string, inviteCode: string) => Promise<void>;
  onLabels: (labels: Record<number, Answer>) => Promise<void>;
  onOpen: (id: number) => void;
  onClose: () => void; // the run off the screen
}

const KEYS: Record<string, Answer> = { y: "yes", n: "no", c: "unclear" };

// Making a survey item in the app (docs/specs/2026-10-29-agent-items-design.md 1): what to record, the agent's steps
// as they come, ten photos to answer when the city publishes no list to test against, and what was added.
export default function AddItemPanel(props: Props) {
  const { run } = props;
  const [open, setOpen] = useState(props.defaultOpen ?? !!run);
  useEffect(() => {
    if (run) setOpen(true);
  }, [run?.id]);
  return (
    <section className="add-item" aria-labelledby="add-item-title">
      <button type="button" className="add-item-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span id="add-item-title">+ Add a survey item</span>
        <span className="hint small">Muse Spark writes it, tests it, and surveys these photos for it</span>
      </button>
      {open && (run ? <RunView {...props} run={run} /> : <Start {...props} />)}
    </section>
  );
}

function Start({ canMake, examples, onStart, onOpen }: Props) {
  const [target, setTarget] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onStart(target.trim(), code.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="add-item-body">
      {examples.length > 0 && (
        <p className="example-runs">
          {examples.map((x) => (
            <button key={x.id} type="button" className="link-button inline" onClick={() => onOpen(x.id)}>
              See the example: {x.target}
            </button>
          ))}
        </p>
      )}
      {canMake ? (
        <form onSubmit={submit} className="add-item-form">
          <label className="field">
            What should the survey record?
            <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="e.g. bike rack, public bench"
              minLength={2} maxLength={60} required />
          </label>
          <label className="field">
            Invite code
            <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" required />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="primary" disabled={busy || target.trim().length < 2}>
            {busy ? "Starting…" : "Write and test the item"}
          </button>
          <span className="hint small">Up to $1 of model calls. A person may be asked about ten photos.</span>
        </form>
      ) : (
        <p className="hint small">Making a new item needs an invite code on the live site; this site shows example runs.</p>
      )}
    </div>
  );
}

function Steps({ lines, active }: { lines: string[]; active: boolean }) {
  return (
    <ol className="item-progress" aria-live="polite">
      {lines.map((line, i) => <li key={i}>{line}</li>)}
      {active && <li className="item-progress-now">Working…</li>}
    </ol>
  );
}

function RunView({ run, onLabels, onClose }: Props & { run: ItemRun }) {
  const active = run.status === "queued" || run.status === "running";
  return (
    <div className="add-item-body">
      <p className="eyebrow">New survey item, written by Muse Spark</p>
      <h3 className="add-item-target">{capital(run.target)}</h3>
      <Steps lines={run.progress} active={active} />
      {run.status === "labels_needed" && <Labels run={run} onLabels={onLabels} />}
      {run.status === "failed" && (
        <>
          <p className="error" role="alert">{run.error}</p>
          <button type="button" onClick={onClose}>Try another item</button>
        </>
      )}
      {run.status === "done" && <Done run={run} onClose={onClose} />}
      <p className="hint small">Model calls so far {dollars(run.cost_usd)} of the $1.00 cap</p>
    </div>
  );
}

function Labels({ run, onLabels }: { run: ItemRun; onLabels: Props["onLabels"] }) {
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const n = answers.length;
  const photo = run.photos[Math.min(n, run.photos.length - 1)];

  async function answer(a: Answer) {
    if (busy || n >= run.photos.length) return;
    const next = [...answers, a];
    setAnswers(next);
    if (next.length < run.photos.length) return;
    setBusy(true);
    setError(null);
    try {
      await onLabels(Object.fromEntries(run.photos.map((p, i) => [p.idx, next[i]])));
    } catch (err) {
      setError((err as Error).message);
      setAnswers(next.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null;
      if (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      const a = KEYS[ev.key.toLowerCase()];
      if (!a || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      ev.preventDefault();
      answer(a);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!photo) return null;
  return (
    <div className="label-card">
      <p className="label-head">
        <strong>Does this photo show a {run.target}?</strong>
        <span>{Math.min(n + 1, run.photos.length)} of {run.photos.length}</span>
      </p>
      <span className="bar" aria-hidden="true"><span style={{ width: `${(n / run.photos.length) * 100}%` }} /></span>
      <p className="hint small">Your answers are what the agent tests its wording on.</p>
      <img src={photo.url} alt={`Street photo ${n + 1} of ${run.photos.length}`} />
      <p className="credit">
        Photo {photoMonth(photo.captured_at)} by {photo.creator ?? "a Mapillary contributor"} ·{" "}
        <a href={photo.link} target="_blank" rel="noreferrer">Mapillary</a> · CC BY-SA 4.0
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="label-answers">
        <button type="button" disabled={busy} onClick={() => answer("yes")}><kbd>Y</kbd> Yes</button>
        <button type="button" disabled={busy} onClick={() => answer("no")}><kbd>N</kbd> No</button>
        <button type="button" disabled={busy} onClick={() => answer("unclear")}><kbd>C</kbd> Can't tell</button>
      </div>
      {n > 0 && n < run.photos.length && (
        <button type="button" className="link-button inline" onClick={() => setAnswers(answers.slice(0, -1))}>
          ‹ Back one photo
        </button>
      )}
    </div>
  );
}

function Done({ run, onClose }: { run: ItemRun; onClose: () => void }) {
  const t = run.test;
  return (
    <div className="agent-done">
      <p><strong>Muse Spark added “{run.target}”</strong> for {dollars(run.cost_usd)}</p>
      {t?.kept && t.false && (
        <p className="hint small">
          Kept {t.kept[0]} of {t.kept[1]} photos showing it and {t.false[0]} of {t.false[1]} without it, on
          answers from {t.labels === "city" && t.layer ? t.layer : "a person"}
        </p>
      )}
      {t?.city && <p className="hint small">{t.city.found} of {t.city.covered} in {t.city.layer}</p>}
      <button type="button" className="link-button inline" onClick={onClose}>Take it off the map</button>
    </div>
  );
}
