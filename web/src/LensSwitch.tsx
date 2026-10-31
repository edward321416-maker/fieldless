export type Lens = "findings" | "records";

// What the map shows: the survey's findings, or the same findings against the city's own records (each finding's
// pairing, and the records in view the survey did not find), paired with an inline HUD legend bar on the map.
export default function LensSwitch({ lens, onChange }: { lens: Lens; onChange: (lens: Lens) => void }) {
  return (
    <div className="map-hud">
      <div className="lens-switch" role="group" aria-label="What the map shows">
        <button type="button" aria-pressed={lens === "findings"} onClick={() => onChange("findings")}>Findings</button>
        <button type="button" aria-pressed={lens === "records"} onClick={() => onChange("records")}>City records</button>
      </div>
      <div className="map-hud-key" aria-hidden="true">
        {lens === "findings" ? (
          <>
            <span className="hud-pill"><i className="hud-dot" />1 dot = 1 photo detection</span>
            <span className="hud-sep">·</span>
            <span className="hud-pill">Click any dot to open photo</span>
          </>
        ) : (
          <>
            <span className="hud-pill"><i className="shape shape-in" />Matched</span>
            <span className="hud-pill"><i className="shape shape-not" />New</span>
            <span className="hud-pill"><i className="shape shape-missed" />To check</span>
          </>
        )}
      </div>
    </div>
  );
}
