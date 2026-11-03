import { count } from "./format";

export type Lens = "findings" | "records" | "update";

// What the map shows: the survey's findings, the same findings against the city's own records (each finding's pairing,
// and the records in view the survey did not find), or the update list's site visits (which records need one), paired
// with an inline HUD legend bar on the map. The site visits' button needs a survey with an update list.
export default function LensSwitch({ lens, onChange, update }: { lens: Lens; onChange: (lens: Lens) => void;
  update?: { noVisit: number; visit: number; unreached?: number } }) {
  const far = update?.unreached ? (
    <span className="hud-pill hud-hint"><i className="shape shape-unreached" />{count(update.unreached)} not reached by photos</span>
  ) : null;
  return (
    <div className="map-hud">
      <div className="lens-switch" role="group" aria-label="What the map shows">
        <button type="button" aria-pressed={lens === "findings"} onClick={() => onChange("findings")}>Findings</button>
        <button type="button" aria-pressed={lens === "records"} onClick={() => onChange("records")}>City records</button>
        {update && <button type="button" aria-pressed={lens === "update"} onClick={() => onChange("update")}>Site visits</button>}
      </div>
      <div className="map-hud-key" aria-hidden="true">
        {lens === "findings" && (
          <>
            <span className="hud-pill"><i className="hud-dot" />1 dot = 1 photo detection</span>
            <span className="hud-sep hud-hint">·</span>
            <span className="hud-pill hud-hint">Click any dot to open photo</span>
            {far}
          </>
        )}
        {lens === "records" && (
          <>
            <span className="hud-pill"><i className="shape shape-in" />Matched</span>
            <span className="hud-pill"><i className="shape shape-not" />Not on list</span>
            <span className="hud-pill"><i className="shape shape-missed" />To check</span>
            {far}
          </>
        )}
        {lens === "update" && update && (
          <>
            <span className="hud-pill"><i className="shape shape-visit" />{update.visit} visit on site</span>
            <span className="hud-pill"><i className="shape shape-no-visit" />{update.noVisit} desk review {update.noVisit === 1 ? "candidate" : "candidates"}</span>
            {update.unreached ? <span className="hud-pill"><i className="shape shape-unreached" />{count(update.unreached)} not reached</span> : null}
          </>
        )}
      </div>
    </div>
  );
}
