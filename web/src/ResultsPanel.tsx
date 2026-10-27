import { dollars, labelText, minutes, photoMonth } from "./format";
import type { Picked } from "./MapView";
import type { Entrance, Issue, Scan } from "./types";

interface Props {
  scan: Scan;
  entrances: Entrance[];
  issues: Issue[];
  showEntrances: boolean;
  showIssues: boolean;
  onShowEntrances: (v: boolean) => void;
  onShowIssues: (v: boolean) => void;
  onPick: (p: Picked) => void;
  onReview: () => void;
}

function IssueButton({ issue, onPick }: { issue: Issue; onPick: (p: Picked) => void }) {
  const status = issue.review_status === "needs_review" ? "To review" : issue.durable ? "" : photoMonth(issue.captured_at);
  return (
    <li>
      <button type="button" className={`item item-${issue.issue_type}`} onClick={() => onPick({ kind: "issue", item: issue })}>
        <span className="item-icon" aria-hidden="true">{issue.label.icon}</span>
        <span className="item-name">{issue.label.text}</span>
        <span className="item-label">{status}</span>
      </button>
    </li>
  );
}

export default function ResultsPanel(props: Props) {
  const defects = props.issues.filter((i) => i.durable);
  const once = props.issues.filter((i) => !i.durable);
  const { counts } = props.scan;
  return (
    <section className="panel-section" aria-labelledby="results-title">
      <h2 id="results-title">2. Survey results</h2>
      <dl className="stats">
        <dt>Street photos surveyed</dt>
        <dd>{props.scan.image_count}</dd>
        <dt>Processing time</dt>
        <dd>{minutes(props.scan.processing_seconds)}</dd>
        <dt>Your review time</dt>
        <dd>{minutes(props.scan.review_seconds)}</dd>
        <dt>Cost</dt>
        <dd>{dollars(props.scan.cost_usd)}</dd>
        <dt>To confirm (?)</dt>
        <dd>{counts.needs_review}</dd>
      </dl>
      <h3>What the survey found</h3>
      <dl className="stats">
        <dt>Tactile paving defects</dt>
        <dd>{counts.defects}</dd>
        <dt>Obstacles on tactile paving (at photo time)</dt>
        <dd>{counts.obstructions}</dd>
        <dt>Entrances with steps</dt>
        <dd>{counts.entrances_with_steps} of {counts.entrances}</dd>
      </dl>
      {counts.needs_review > 0 && (
        <button type="button" className="primary" onClick={props.onReview}>
          Confirm {counts.needs_review} finding{counts.needs_review === 1 ? "" : "s"}
        </button>
      )}
      <fieldset>
        <legend>Map layers</legend>
        <label>
          <input type="checkbox" checked={props.showIssues} onChange={(e) => props.onShowIssues(e.target.checked)} />
          Sidewalk
        </label>
        <label>
          <input type="checkbox" checked={props.showEntrances} onChange={(e) => props.onShowEntrances(e.target.checked)} />
          Entrances
        </label>
      </fieldset>
      <h3>Tactile paving defects</h3>
      {defects.length === 0 ? (
        <p>No missing blocks, covers or fixed objects found.</p>
      ) : (
        <ul className="item-list">{defects.map((i) => <IssueButton key={`i${i.id}`} issue={i} onPick={props.onPick} />)}</ul>
      )}
      <h3>Obstacles seen once</h3>
      <p className="hint">Movable objects photographed on the tactile paving; each is a fact at the photo date.</p>
      {once.length === 0 ? (
        <p>None found.</p>
      ) : (
        <ul className="item-list">{once.map((i) => <IssueButton key={`i${i.id}`} issue={i} onPick={props.onPick} />)}</ul>
      )}
      <h3>Entrances</h3>
      <ul className="item-list">
        {props.entrances.map((e) => (
          <li key={`e${e.id}`}>
            <button type="button" className="item item-entrance" onClick={() => props.onPick({ kind: "entrance", item: e })}
              aria-label={`${e.name || "Unnamed entrance"}: ${labelText(e.label)}`}>
              <span className="item-icon" aria-hidden="true">{e.label.icon}</span>
              <span className="item-name">{e.name || "Unnamed entrance"}</span>
              <span className="item-label">{e.label.text}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
