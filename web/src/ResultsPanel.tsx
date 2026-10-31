import type { ReactNode } from "react";
import ActionHub, { type ActionTab } from "./ActionHub";
import { filesUrl, reportUrl, STATIC_DEMO } from "./api";
import { BENCHMARKS } from "./benchmarks";
import type { CityItemCounts, CityState, MissedRecord } from "./cityMatch";
import { isFinished, yearsSpan } from "./format";
import { colourOf, itemName, type Finding } from "./items";
import type { Lens } from "./LensSwitch";
import ShareButton from "./ShareButton";
import { checkLine, cityLine, headline, itemNote, statusNote, unsureCount } from "./summary";
import type { Scan } from "./types";

interface Props {
  scan: Scan;
  name: string | null; // the demo area's name, when it is one
  findings: Finding[];
  hidden: Set<string>; // items switched off on the map
  onToggle: (item: string) => void;
  onOpenItem: (item: string) => void; // an item's findings, one by one (FindingList)
  onReview: () => void;
  canReview?: boolean; // false on an example survey, which is read-only
  onIntro: () => void; // back to what Fieldless does
  hasReport: boolean; // the server has the survey's results report (/api/config report)
  agentCity?: Record<string, { found: number; covered: number }>; // an agent item's rows against the city's list
  lens?: Lens; // what the map shows; "records": each row says how its findings pair with the city's records
  cityCounts?: Record<string, CityItemCounts>; // per item with a city list (cityMatch.cityCounts)
  streets?: Record<string, string>;
  cityStates?: Record<string, CityState>;
  actionTab?: ActionTab;
  onPickFinding?: (f: Finding) => void;
  onPickRecord?: (r: MissedRecord) => void;
  onSwitchLens?: (l: Lens) => void;
  children?: ReactNode; // the add-item panel, above the files
}

// A row's line in the city-records lens: the three numbers the map's three shapes show. An agent's item is compared
// by its own run, which counts the city's records found, not each finding's pairing.
function recordsNote(c: CityItemCounts | undefined, agent: { found: number; covered: number } | undefined): string {
  if (c) return `${c.matched} matched · ${c.notInList} not in the city's list · ${c.missed} to check`;
  return (agent && cityLine(agent, true)?.text) ?? "No city list to compare";
}

// A finished survey (docs/specs/2026-10-29-cleanup-design.md 4): one number on top, one row per item that is also the
// map's legend and filter (Project Sidewalk's label map; docs/ui-rules.md U2, U4), what a person did, and the files.
export default function ResultsPanel(props: Props) {
  const { scan, findings } = props;
  const items = scan.items;
  const city = scan.city_check;
  const top = headline(scan, BENCHMARKS[scan.standard]);
  const present = items.filter((it) => findings.some((f) => f.item === it.id));
  const recordsLens = props.lens === "records";
  const unsure = unsureCount(scan.counts);

  return (
    <section className="panel-section results" aria-labelledby="results-title">
      <button type="button" className="link-button back-intro" onClick={props.onIntro}>← What Fieldless does</button>
      <div className="results-header-card">
        {props.name && <p className="eyebrow">Example survey</p>}
        <div className="results-title-row">
          <h2 id="results-title">{props.name ?? `Survey ${scan.id}`}</h2>
          <ShareButton />
        </div>
        <p className="sub">{scan.image_count} public street photos, taken {yearsSpan(scan.captured_from, scan.captured_to)}</p>
        {statusNote(scan) && <p className="error" role="status">{statusNote(scan)}</p>}
      </div>

      <div className="headline-one">
        <div className="headline-main">
          <strong>{top.big} <small>{top.unit}</small></strong>
          <span className="headline-meta">{top.line}</span>
        </div>
        {top.foot && (
          <span className="hint small headline-bench">
            {top.foot} · <a href={BENCHMARKS[scan.standard]!.source} target="_blank" rel="noreferrer"
              title={BENCHMARKS[scan.standard]!.caveat}>Source</a>
          </span>
        )}
      </div>

      <div className="found-head">
        <div className="found-head-top">
          <h3>{findings.length} findings</h3>
          {city && (
            <a className="small method-pill" href={city.method_url} target="_blank" rel="noreferrer"
              title={`Measured offline on ${city.measured_at} (${city.city})`}>How the city list check works</a>
          )}
        </div>
        <span className="found-subhint">Toggle colour dot to filter map · Click row to inspect</span>
      </div>
      {recordsLens && (
        <div className="lens-legend">
          <ul>
            <li><span className="shape shape-in" aria-hidden="true" />Found and in the city's list</li>
            <li><span className="shape shape-not" aria-hidden="true" />Found, not in the city's list</li>
            <li><span className="shape shape-missed" aria-hidden="true" />In the city's list, not found (to check)</li>
          </ul>
          <p className="hint small">Paired by photo position and viewing direction</p>
        </div>
      )}
      <ul className="item-rows">
        {present.map((it) => {
          const mine = findings.filter((f) => f.item === it.id);
          const name = itemName(it.id, items);
          const on = !props.hidden.has(it.id);
          const colour = colourOf(it.id, items);
          const agentCity = props.agentCity?.[it.id];
          const cityInfo = agentCity ? cityLine(agentCity, true) : cityLine(city?.items[it.id], !!city);
          const note = recordsLens ? recordsNote(props.cityCounts?.[it.id], agentCity) : itemNote({
            city: cityInfo?.text ?? null,
            confirmed: mine.filter((f) => f.badge === "defect" && f.status === "verified").length,
            possible: mine.filter((f) => f.badge === "defect" && f.status !== "verified").length,
            site: mine.filter((f) => f.badge === "site").length,
          });
          const sharePct = cityInfo?.share !== null && cityInfo?.share !== undefined
            ? Math.round(Math.min(1, Math.max(0, cityInfo.share)) * 100)
            : null;
          return (
            <li key={it.id} className={`item-row${on ? "" : " item-off"}`}>
              <button type="button" className="dot-toggle" aria-pressed={on}
                aria-label={`${on ? "Hide" : "Show"} ${name} on the map`} onClick={() => props.onToggle(it.id)}>
                <span className="dot" style={{ background: on ? colour : "transparent",
                  borderColor: colour }} />
              </button>
              <button type="button" className="item-open" onClick={() => props.onOpenItem(it.id)}>
                <span className="item-text">
                  <span className="item-name-line">
                    <span className="item-name">{name}</span>
                    {it.added_by === "agent" && <span className="agent-badge">AI Custom</span>}
                  </span>
                  {it.added_by === "agent" && (
                    <span className="agent-line">Added by the agent · not in the survey standard yet</span>
                  )}
                  {(note || (!recordsLens && sharePct !== null)) && (
                    <span className="item-sub-row">
                      {!recordsLens && sharePct !== null && (
                        <span className="city-match-bar" aria-hidden="true">
                          <span className="city-match-fill" style={{ width: `${sharePct}%`, background: colour }} />
                        </span>
                      )}
                      {note && <span className="item-note">{note}</span>}
                    </span>
                  )}
                </span>
                <span className="item-count">{mine.length}</span>
                <span className="chevron" aria-hidden="true">›</span>
              </button>
            </li>
          );
        })}
      </ul>

      <ActionHub
        scan={scan}
        findings={findings}
        streets={props.streets}
        cityStates={props.cityStates}
        lens={props.lens}
        initialTab={props.actionTab}
        onPickFinding={props.onPickFinding}
        onPickRecord={props.onPickRecord}
        onSwitchLens={props.onSwitchLens}
      />

      <details className="accordion-card check-drawer">
        <summary className="accordion-summary">
          <span className="accordion-title">Model confidence</span>
          <span className="accordion-meta">{unsure > 0 ? `${unsure} flagged` : "All high confidence"}</span>
        </summary>
        <div className="check-line">
          <p>{checkLine(scan.counts)}</p>
          {scan.counts.needs_review > 0 && props.canReview && !STATIC_DEMO && (
            <button type="button" onClick={props.onReview}>Review</button>
          )}
        </div>
      </details>

      {props.children}

      {isFinished(scan) && (
        <div className="download">
          <div className="download-actions">
            {props.hasReport && (
              <a className="button-link report-link" href={reportUrl(scan.id)} target="_blank" rel="noreferrer">Survey report</a>
            )}
            <a className="button-link" href={filesUrl(scan.id)} download>Download for a GIS</a>
          </div>
          <span className="hint small">GeoJSON and a CSV per item, and a one-page report with every photo credited</span>
        </div>
      )}
    </section>
  );
}
