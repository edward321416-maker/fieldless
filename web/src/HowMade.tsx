import { modelCalls, stageRows, type CallStats } from "./callStats";
import { dollars } from "./format";

// How a finished survey was made: each stage of the run, which Meta model did it, how many calls and for how much,
// so the division of work between SAM 3.1, Muse Spark and the open-weight models is on screen, not only in the code.
export default function HowMade({ stats }: { stats: CallStats }) {
  const rows = stageRows(stats);
  return (
    <section className="panel-section how-made" aria-labelledby="how-made-title">
      <h3 id="how-made-title">How this survey was made</h3>
      <p className="sub">
        {modelCalls(stats)} model calls · {dollars(stats.total.cost_usd)} in all ·{" "}
        {(stats.total.processing_seconds / 60).toFixed(1)} min of processing
      </p>
      <ol className="stage-rows">
        {rows.map((r) => (
          <li key={r.stage}>
            <div className="stage-head">
              <span className="stage-label">{r.label}</span>
              <span className="stage-cost">{r.cost === null ? "cost not logged" : dollars(r.cost)}</span>
            </div>
            <div className="stage-bar" aria-hidden="true"><span style={{ width: `${Math.round(r.share * 100)}%` }} /></div>
            <span className="stage-models">{r.models.map((m) => `${m.model} × ${m.calls}`).join(" · ")}</span>
          </li>
        ))}
      </ol>
      <p className="hint small">
        Calls run side by side, so their own time adds up to more than the processing time. File uploads for SAM 3.1
        video are free and not listed.
      </p>
    </section>
  );
}
