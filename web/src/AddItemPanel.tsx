import { useEffect, useState } from "react";
import { dollars } from "./format";
import { capital } from "./items";
import ShareButton from "./ShareButton";
import type { ItemRun, ItemRunSummary } from "./types";

interface Props {
  canMake: boolean; // the server makes items (an invite code is set) and this is not the static demo
  run: ItemRun | null; // the run on screen, for this survey
  examples: ItemRunSummary[]; // the survey's finished runs
  defaultOpen?: boolean;
  onStart: (target: string, inviteCode: string) => Promise<void>;
  onOpen: (id: number) => void;
  onClose: () => void; // the run off the screen
}

// Making a survey item in the app (docs/specs/2026-10-29-agent-items-design.md 1): what to record, the agent's steps
// as they come, and what was added. No person is asked anything: another model answers the photos it is tested on.
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
          <span className="hint small">Up to $1 of model calls. Muse Spark Open answers the photos it is tested on.</span>
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

function RunView({ run, onClose }: Props & { run: ItemRun }) {
  const active = run.status === "queued" || run.status === "running";
  return (
    <div className="add-item-body">
      <p className="eyebrow">New survey item, written by Muse Spark</p>
      <h3 className="add-item-target">{capital(run.target)}</h3>
      <Steps lines={run.progress} active={active} />
      {run.status === "failed" && (
        <>
          <p className="error" role="alert">{run.error}</p>
          <button type="button" onClick={onClose}>Try another item</button>
        </>
      )}
      {run.status === "done" && <Done run={run} onClose={onClose} />}
      {run.status !== "done" && <p className="hint small">Model calls so far {dollars(run.cost_usd)} of the $1.00 cap</p>}
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
          Kept {t.kept[0]} of {t.kept[1]} photos showing it and {t.false[0]} of {t.false[1]} without it, on{" "}
          {t.judge ?? "another model"}'s answers
        </p>
      )}
      {t?.city && <p className="hint small">{t.city.found} of {t.city.covered} in {t.city.layer}</p>}
      <div className="agent-done-actions">
        <ShareButton label="Share this survey with its new item" />
        <button type="button" className="link-button inline" onClick={onClose}>Take it off the map</button>
      </div>
    </div>
  );
}
