export type Lens = "findings" | "records";

// What the map shows: the survey's findings, or the same findings against the city's own records (each finding's
// pairing, and the records in view the survey did not find).
export default function LensSwitch({ lens, onChange }: { lens: Lens; onChange: (lens: Lens) => void }) {
  return (
    <div className="lens-switch" role="group" aria-label="What the map shows">
      <button type="button" aria-pressed={lens === "findings"} onClick={() => onChange("findings")}>Findings</button>
      <button type="button" aria-pressed={lens === "records"} onClick={() => onChange("records")}>City records</button>
    </div>
  );
}
