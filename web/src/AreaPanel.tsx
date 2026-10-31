import { useEffect, useState } from "react";
import { api, ApiError, STATIC_DEMO } from "./api";
import { bboxTooLarge, bboxTooSmall } from "./bbox";
import { dollars, isFinished, stageState, yearsSpan } from "./format";
import { itemName } from "./items";
import { itemArt } from "./markerArt";
import { statusNote } from "./summary";
import type { Bbox, Preview, Scan } from "./types";

interface Props {
  bbox: Bbox | null;
  selecting: boolean;
  maxDeg2: number;
  inviteRequired: boolean;
  scan: Scan | null;
  onToggleSelect: () => void;
  onUseView: () => void;
  onStarted: (scanId: number) => void;
  onPreview: (p: Preview | null) => void; // the map draws the walks the survey would follow
}

const STEPS = ["Area", "Preview", "Running", "Results"];

// Where a new survey stands (docs/specs/2026-10-28-ui-redesign-design.md 3.5): the four steps, the current one marked.
function Steps({ at }: { at: number }) {
  return (
    <ol className="steps" aria-label="Steps of a new survey">
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "step-done" : i === at ? "step-now" : ""} aria-current={i === at ? "step" : undefined}>
          <span className="step-n" aria-hidden="true">{i < at ? "✓" : i + 1}</span> {s}
        </li>
      ))}
    </ol>
  );
}

const STATE_ICON: Record<string, string> = { done: "✓", running: "…", failed: "✕", stopped: "■", pending: "○" };

function StageList({ scan }: { scan: Scan }) {
  return (
    <ol>
      {stageState(scan).map((s) => (
        <li key={s.name} className={`stage stage-${s.state}`}>
          <span aria-hidden="true">{STATE_ICON[s.state] ?? "•"}</span> {s.label}
          <span className="visually-hidden"> ({s.state})</span>
        </li>
      ))}
    </ol>
  );
}

export default function AreaPanel(props: Props) {
  const { bbox, maxDeg2 } = props;
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [invite, setInvite] = useState("");
  const [starting, setStarting] = useState(false);
  const [waited, setWaited] = useState(0); // seconds the preview has taken so far
  const running = props.scan !== null && (props.scan.status === "queued" || props.scan.status === "running");

  useEffect(() => {
    setPreview(null);
    props.onPreview(null);
    setMessage(null);
    setLoading(false);
    if (!bbox) return;
    if (bboxTooSmall(bbox)) {
      setMessage("Drag a rectangle over the streets you want to survey.");
      return;
    }
    if (bboxTooLarge(bbox, maxDeg2)) {
      setMessage("This area is too large. Choose a rectangle about 1 km across or smaller.");
      return;
    }
    if (STATIC_DEMO) {
      setMessage("Area selected on the map. Connect a live Fieldless server to count Mapillary photos and start a new scan here.");
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.preview(bbox)
      .then((p) => {
        if (cancelled) return;
        setPreview(p);
        props.onPreview(p);
      })
      .catch((e: Error) => !cancelled && setMessage(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [bbox, maxDeg2]);

  // Counting the photos asks Mapillary for every walk in the area: 20-50 s. Say so, and count, so it never looks stuck.
  useEffect(() => {
    if (!loading) return;
    setWaited(0);
    const t = window.setInterval(() => setWaited((w) => w + 1), 1000);
    return () => window.clearInterval(t);
  }, [loading]);

  async function start() {
    if (!bbox || starting) return;
    setMessage(null);
    setStarting(true);
    try {
      const { id } = await api.startScan(bbox, invite);
      props.onStarted(id);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not start the survey.");
    } finally {
      setStarting(false);
    }
  }

  const finished = props.scan !== null && isFinished(props.scan);
  const step = props.scan ? (finished ? 3 : 2) : preview ? 1 : 0;
  const stages = props.scan ? stageState(props.scan) : [];
  const done = stages.filter((s) => s.state === "done").length;
  const items = preview?.items ?? [];
  const art = itemArt(items);
  return (
    <section className="panel-section" aria-labelledby="area-title">
      <h2 id="area-title">New survey</h2>
      <Steps at={step} />
      {step === 0 && (
        <p className="lead">Search any street or neighborhood in the top bar, or draw a box directly on the map. Nothing runs until you press Start.</p>
      )}
      <div className="row">
        <button type="button" className={props.selecting ? "" : "primary"} onClick={props.onToggleSelect}
          aria-pressed={props.selecting}>
          {props.selecting ? "Cancel drawing" : "Draw a box"}
        </button>
        <button type="button" onClick={props.onUseView}>Use this map view</button>
      </div>
      {props.selecting && <p className="hint">Drag on the map to draw a box; release it to count the photos inside. On a phone, move the map to the streets and use "Use this map view".</p>}
      {step === 0 && !props.selecting && !bbox && (
        <ol className="next-steps">
          <li>You see how many photos the box holds, when they were taken, and the estimated cost.</li>
          <li>Start, and SAM 3.1 and Muse Spark go through the photos. A survey is capped at $3.00.</li>
        </ol>
      )}
      {loading && (
        <p role="status">Counting the public street photos of this area on Mapillary… {waited} s (usually under a minute)</p>
      )}
      {message && <p className="error" role="alert">{message}</p>}
      {preview && (
        <div role="status">
          {preview.photos === 0 ? (
            <p>Not enough recent pedestrian photos here. Try a nearby street.</p>
          ) : (
            <>
              <dl className="stats">
                <dt>Street photos</dt>
                <dd>{preview.photos} in {preview.sequences} walk{preview.sequences === 1 ? "" : "s"}</dd>
                <dt>Taken</dt>
                <dd>{yearsSpan(preview.captured_from, preview.captured_to)}</dd>
                <dt>Cost</dt>
                <dd>est. {dollars(preview.est_cost_usd)}{preview.cap_usd ? ` (cap ${dollars(preview.cap_usd)})` : ""}</dd>
              </dl>
              {items.length > 0 && (
                <>
                  <p className="hint small">What the survey will look for ({preview.standard} survey items):</p>
                  <p className="chips">
                    {items.map((it) => (
                      <span key={it.id} className="chip item-chip">
                        <img src={art[it.id]} alt="" width={20} height={20} /> {itemName(it.id, items)}
                      </span>
                    ))}
                  </p>
                </>
              )}
            </>
          )}
          {props.inviteRequired && preview.photos > 0 && (
            <label className="field">
              Invite code
              <input value={invite} onChange={(e) => setInvite(e.target.value)} autoComplete="off" />
            </label>
          )}
          {preview.photos > 0 && (
            <p className="hint">Starting sends these street photos to Meta's Muse Spark and SAM 3.1 for analysis.</p>
          )}
          <button type="button" className="primary" onClick={start} disabled={preview.photos === 0 || running || starting}>
            Start survey
          </button>
        </div>
      )}
      {props.scan && (
        <div className="progress" aria-live="polite">
          {isFinished(props.scan) ? (
            <details>
              <summary>Survey {props.scan.id}: {props.scan.status.replace(/_/g, " ")}</summary>
              <StageList scan={props.scan} />
            </details>
          ) : (
            <>
              <h3>Survey {props.scan.id}: {props.scan.status.replace(/_/g, " ")}</h3>
              <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={stages.length} aria-valuenow={done}
                aria-label="Steps done">
                <span style={{ width: `${(100 * done) / Math.max(1, stages.length)}%` }} />
              </div>
              <p className="hint small">{done} of {stages.length} steps · {dollars(props.scan.cost_usd)} spent so far</p>
              <StageList scan={props.scan} />
            </>
          )}
          {statusNote(props.scan) && <p className={props.scan.status === "queued" ? "hint" : "error"}>{statusNote(props.scan)}</p>}
          {props.scan.error && (
            <details>
              <summary>Details</summary>
              <p className="hint">{props.scan.error}</p>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
