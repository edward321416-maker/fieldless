import type { ReactNode } from "react";
import { filesUrl, reportUrl, STATIC_DEMO } from "./api";
import { BENCHMARKS } from "./benchmarks";
import type { CityItemCounts } from "./cityMatch";
import { isFinished, yearsSpan } from "./format";
import { colourOf, itemName, type Finding } from "./items";
import type { Lens } from "./LensSwitch";
import ShareButton from "./ShareButton";
import { checkLine, cityLine, headline, itemNote } from "./summary";
import type { Scan } from "./types";

interface Props {
  scan: Scan;
  name: string | null; // the demo area's name, when it is one
  findings: Finding[];
  hidden: Set<string>; // items switched off on the map
  onToggle: (item: string) => void;
  onOpenItem: (item: string) => void; // an item's findings, one by one (FindingList)
  onReview: () => void;
  onIntro: () => void; // back to what Fieldless does
  hasReport: boolean; // the server has the survey's results report (/api/config report)
  agentCity?: Record<string, { found: number; covered: number }>; // an agent item's rows against the city's list
  lens?: Lens; // what the map shows; "records": each row says how its findings pair with the city's records
  cityCounts?: Record<string, CityItemCounts>; // per item with a city list (cityMatch.cityCounts)
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

  return (
    <section className="panel-section results" aria-labelledby="results-title">
      <button type="button" className="link-button back-intro" onClick={props.onIntro}>← What Fieldless does</button>
      {props.name && <p className="eyebrow">Example survey</p>}
      <div className="results-title-row">
        <h2 id="results-title">{props.name ?? `Survey ${scan.id}`}</h2>
        <ShareButton />
      </div>
      <p className="sub">{scan.image_count} public street photos, taken {yearsSpan(scan.captured_from, scan.captured_to)}</p>

      <div className="headline-one">
        <strong>{top.big} <small>{top.unit}</small></strong>
        <span>{top.line}</span>
        {top.foot && (
          <span className="hint small">
            {top.foot} · <a href={BENCHMARKS[scan.standard]!.source} target="_blank" rel="noreferrer"
              title={BENCHMARKS[scan.standard]!.caveat}>Source</a>
          </span>
        )}
      </div>

      <div className="found-head">
        <h3>{findings.length} findings</h3>
        {city && (
          <a className="small" href={city.method_url} target="_blank" rel="noreferrer"
            title={`Measured offline on ${city.measured_at} (${city.city})`}>How the city list check works</a>
        )}
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
          const agentCity = props.agentCity?.[it.id];
          const note = recordsLens ? recordsNote(props.cityCounts?.[it.id], agentCity) : itemNote({
            city: agentCity ? cityLine(agentCity, true)?.text ?? null : cityLine(city?.items[it.id], !!city)?.text ?? null,
            confirmed: mine.filter((f) => f.badge === "defect" && f.status === "verified").length,
            possible: mine.filter((f) => f.badge === "defect" && f.status !== "verified").length,
            site: mine.filter((f) => f.badge === "site").length,
          });
          return (
            <li key={it.id} className={`item-row${on ? "" : " item-off"}`}>
              <button type="button" className="dot-toggle" aria-pressed={on}
                aria-label={`${on ? "Hide" : "Show"} ${name} on the map`} onClick={() => props.onToggle(it.id)}>
                <span className="dot" style={{ background: on ? colourOf(it.id, items) : "transparent",
                  borderColor: colourOf(it.id, items) }} />
              </button>
              <button type="button" className="item-open" onClick={() => props.onOpenItem(it.id)}>
                <span className="item-text">
                  <span className="item-name">{name}</span>
                  {it.added_by === "agent" && (
                    <span className="agent-line">Added by the agent · not in the survey standard yet</span>
                  )}
                  {note && <span className="item-note">{note}</span>}
                </span>
                <span className="item-count">{mine.length}</span>
                <span className="chevron" aria-hidden="true">›</span>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="check-line">
        <p>{checkLine(scan.counts)}</p>
        {scan.counts.needs_review > 0 && !STATIC_DEMO && (
          <button type="button" onClick={props.onReview}>Review</button>
        )}
      </div>

      {props.children}

      {isFinished(scan) && (
        <div className="download">
          {props.hasReport && (
            <a className="button-link report-link" href={reportUrl(scan.id)} target="_blank" rel="noreferrer">Survey report</a>
          )}
          <a className="button-link" href={filesUrl(scan.id)} download>Download for a GIS</a>
          <span className="hint small">GeoJSON and a CSV per item, and a one-page report with every photo credited</span>
        </div>
      )}
    </section>
  );
}
