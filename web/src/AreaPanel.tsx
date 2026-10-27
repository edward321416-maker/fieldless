import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { bboxTooLarge, bboxTooSmall } from "./bbox";
import { dollars, stageState, yearsSpan } from "./format";
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
}

const STATE_ICON: Record<string, string> = { done: "✓", running: "…", failed: "✕", stopped: "■", pending: "○" };

export default function AreaPanel(props: Props) {
  const { bbox, maxDeg2 } = props;
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [invite, setInvite] = useState("");
  const [starting, setStarting] = useState(false);
  const running = props.scan !== null && (props.scan.status === "queued" || props.scan.status === "running");

  useEffect(() => {
    setPreview(null);
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
    let cancelled = false;
    setLoading(true);
    api.preview(bbox)
      .then((p) => !cancelled && setPreview(p))
      .catch((e: Error) => !cancelled && setMessage(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [bbox, maxDeg2]);

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

  return (
    <section className="panel-section" aria-labelledby="area-title">
      <h2 id="area-title">1. Choose streets to survey</h2>
      <div className="row">
        <button type="button" onClick={props.onToggleSelect} aria-pressed={props.selecting}>
          {props.selecting ? "Cancel drawing" : "Draw a rectangle"}
        </button>
        <button type="button" onClick={props.onUseView}>Use this map view</button>
      </div>
      {props.selecting && <p className="hint">Drag on the map to draw the survey area.</p>}
      {loading && <p role="status">Counting street photos…</p>}
      {message && <p className="error" role="alert">{message}</p>}
      {preview && (
        <div role="status">
          {preview.photos === 0 ? (
            <p>Not enough recent pedestrian photos here. Try a nearby street.</p>
          ) : (
            <dl className="stats">
              <dt>Street photos</dt>
              <dd>{preview.photos} in {preview.sequences} walk{preview.sequences === 1 ? "" : "s"}</dd>
              <dt>Taken</dt>
              <dd>{yearsSpan(preview.captured_from, preview.captured_to)}</dd>
              <dt>Estimated cost</dt>
              <dd>{dollars(preview.est_cost_usd)}</dd>
            </dl>
          )}
          {props.inviteRequired && preview.photos > 0 && (
            <label className="field">
              Invite code
              <input value={invite} onChange={(e) => setInvite(e.target.value)} autoComplete="off" />
            </label>
          )}
          <button type="button" className="primary" onClick={start} disabled={preview.photos === 0 || running || starting}>
            Start survey
          </button>
        </div>
      )}
      {props.scan && (
        <div className="progress" aria-live="polite">
          <h3>Survey {props.scan.id}: {props.scan.status.replace(/_/g, " ")}</h3>
          <ol>
            {stageState(props.scan).map((s) => (
              <li key={s.name} className={`stage stage-${s.state}`}>
                <span aria-hidden="true">{STATE_ICON[s.state] ?? "•"}</span> {s.label}
                <span className="visually-hidden"> ({s.state})</span>
              </li>
            ))}
          </ol>
          {props.scan.error && <p className="error">{props.scan.error}</p>}
        </div>
      )}
    </section>
  );
}
