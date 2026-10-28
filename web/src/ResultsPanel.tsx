import { Fragment } from "react";
import { filesUrl, STATIC_DEMO } from "./api";
import { dollars, isFinished, issueTone, km, minutes, photoMonth } from "./format";
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

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const STATUS_TEXT: Record<string, string> = { needs_review: "To review", field_check: "Check on site" };

function IssueButton({ issue, onPick }: { issue: Issue; onPick: (p: Picked) => void }) {
  const parts = [STATUS_TEXT[issue.review_status] ?? "", issue.durable ? "" : photoMonth(issue.captured_at)];
  const status = parts.filter(Boolean).join(" · ");
  return (
    <li>
      <button type="button" className={`item item-${issueTone(issue)}`} onClick={() => onPick({ kind: "issue", item: issue })}>
        <span className="item-icon" aria-hidden="true">{issue.label.icon}</span>
        <span className="item-name">{issue.label.text}</span>
        <span className="item-label">{status}</span>
      </button>
    </li>
  );
}

export default function ResultsPanel(props: Props) {
  const defects = props.issues.filter((i) => i.durable);
  const once = props.issues.filter((i) => i.issue_type === "obstruction");
  const us = props.scan.standard === "US";
  const { counts } = props.scan;
  return (
    <section className="panel-section" aria-labelledby="results-title">
      <h2 id="results-title">2. Survey results</h2>
      <dl className="stats">
        <dt>Street photos surveyed</dt>
        <dd>{props.scan.image_count}</dd>
        {props.scan.km !== null && props.scan.km > 0 && (
          <>
            <dt>Street length covered</dt>
            <dd>{km(props.scan.km)}</dd>
          </>
        )}
        <dt>Processing time</dt>
        <dd>{minutes(props.scan.processing_seconds)}</dd>
        <dt>Review time</dt>
        <dd>{minutes(props.scan.review_seconds)}</dd>
        <dt>Fieldless total</dt>
        <dd>{minutes(props.scan.processing_seconds + props.scan.review_seconds)}</dd>
        <dt>Cost</dt>
        <dd>{dollars(props.scan.cost_usd)}</dd>
        {props.scan.usd_per_km !== null && (
          <>
            <dt>Cost per km</dt>
            <dd>{dollars(props.scan.usd_per_km)}</dd>
          </>
        )}
        <dt>To confirm (?)</dt>
        <dd>{counts.needs_review}</dd>
        {counts.field_check > 0 && (
          <>
            <dt>To check on site</dt>
            <dd>{counts.field_check}</dd>
          </>
        )}
      </dl>
      <h3>What the survey found</h3>
      <dl className="stats">
        {us ? (
          <>
            {props.scan.checks.map((c) => (
              <Fragment key={c.id}>
                {c.inventory ? (
                  <>
                    <dt>{cap(c.target)}s found</dt>
                    <dd>{c.yes}{c.unclear ? ` (+${c.unclear} to confirm)` : ""}</dd>
                  </>
                ) : (
                  <>
                    <dt>{cap(c.target)}s checked</dt>
                    <dd>{c.yes + c.no + c.unclear}</dd>
                    <dt>{c.report_when === "no" ? c.label_no : c.label_yes}</dt>
                    <dd>{c.report_when === "no" ? c.no : c.yes}</dd>
                  </>
                )}
              </Fragment>
            ))}
            <dt>Objects in the walking path (at photo time)</dt>
            <dd>{counts.obstructions}</dd>
          </>
        ) : (
          <>
            <dt>Tactile paving defects</dt>
            <dd>{counts.defects}</dd>
            <dt>Obstacles on tactile paving (at photo time)</dt>
            <dd>{counts.obstructions}</dd>
          </>
        )}
        <dt>Entrances with steps</dt>
        <dd>{counts.entrances_with_steps} of {counts.entrances}</dd>
      </dl>
      {counts.needs_review > 0 && !STATIC_DEMO && (
        <button type="button" className="primary" onClick={props.onReview}>
          Confirm {counts.needs_review} finding{counts.needs_review === 1 ? "" : "s"}
        </button>
      )}
      {isFinished(props.scan) && (
        <p className="download">
          <a href={filesUrl(props.scan.id)} download>Download the survey files</a>: a one-page report, GeoJSON and a
          CSV per item for a GIS, and the evidence photos.
        </p>
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
      {us ? (
        props.scan.checks.map((c) => {
          const rows = props.issues.filter((i) => i.issue_type === c.id);
          return (
            <Fragment key={c.id}>
              <h3>{cap(c.target)}s</h3>
              {rows.length === 0 ? (
                <p>None found.</p>
              ) : (
                <ul className="item-list">{rows.map((i) => <IssueButton key={`i${i.id}`} issue={i} onPick={props.onPick} />)}</ul>
              )}
            </Fragment>
          );
        })
      ) : (
        <>
          <h3>Tactile paving defects</h3>
          {defects.length === 0 ? (
            <p>No missing blocks, covers or fixed objects found.</p>
          ) : (
            <ul className="item-list">{defects.map((i) => <IssueButton key={`i${i.id}`} issue={i} onPick={props.onPick} />)}</ul>
          )}
        </>
      )}
      <h3>{us ? "Objects in the walking path" : "Obstacles seen once"}</h3>
      <p className="hint">
        {us
          ? "Movable objects photographed on the walking path; each is a fact at the photo date."
          : "Movable objects photographed on the tactile paving; each is a fact at the photo date."}
      </p>
      {once.length === 0 ? (
        <p>None found.</p>
      ) : (
        <ul className="item-list">{once.map((i) => <IssueButton key={`i${i.id}`} issue={i} onPick={props.onPick} />)}</ul>
      )}
      <h3>Entrances</h3>
      {props.entrances.length === 0 ? (
        <p>None found.</p>
      ) : (
        <ul className="item-list">
          {props.entrances.map((e) => (
            <li key={`e${e.id}`}>
              <button type="button" className="item item-entrance" onClick={() => props.onPick({ kind: "entrance", item: e })}
                aria-label={`${e.name || "Unnamed entrance"}: ${e.label.text}`}>
                <span className="item-icon" aria-hidden="true">{e.label.icon}</span>
                <span className="item-name">{e.name || "Unnamed entrance"}</span>
                <span className="item-label">{e.label.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
