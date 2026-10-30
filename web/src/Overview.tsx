import { BENCHMARKS } from "./benchmarks";
import { CHECKED_BY_EYE } from "./claims";
import { cityTotals } from "./cityMatch";
import { dollars } from "./format";
import { capital, colourOf, itemName } from "./items";
import { phraseJourney, secondOpinions } from "./overviewData";
import { cityLine, headline, thumbPosition } from "./summary";
import type { Issue, ItemRun, Scan } from "./types";

export interface Example {
  item: string;
  url: string; // the evidence photo
  box: number[] | null;
}

interface Props {
  scan: Scan | null; // the first example survey
  name: string | null; // its area's name
  issues: Issue[]; // its findings (not the agent items')
  counts: Record<string, number>; // findings per item id, entrances included
  examples: Example[]; // one photo per item
  runs: ItemRun[]; // its finished item runs, at most two
  others: { scan: Scan; name: string }[]; // the other example surveys
  canSurvey: boolean; // false on the static demo
  onSee: () => void;
  onSurvey: () => void;
  onOpenScan: (id: number) => void;
  onOpenRun: (scanId: number, runId: number) => void;
}

const STANDARD: Record<Scan["standard"], string> = { US: "US", KR: "Korean" };
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A survey's first city list, the one its cards name ("6 of 23 in" the list's own name).
const firstList = (scan: Scan) => (scan.city_check ? Object.values(scan.city_check.items)[0] ?? null : null);

// What a first visit sees: every part of a survey on one page before the map, in the order a reader asks about it.
// The cost per km comes first; then each item at the same size, how the rules, the city's records and the photos
// meet, what backs the numbers, the items the agent wrote, and the example surveys. Each number is the server's.
export default function Overview(props: Props) {
  const { scan, name, canSurvey } = props;
  const bench = scan ? BENCHMARKS[scan.standard] : undefined;
  return (
    <section className="overview" aria-labelledby="overview-title">
      <div className="overview-opening">
        <h2 id="overview-title">Street surveys without the fieldwork</h2>
        <p className="overview-lead">
          Cities send people to walk every block and write down what is on the street. Fieldless reads the public street
          photos that already exist and maps the same things, each with its photo.
        </p>
        {scan && scan.usd_per_km !== null && (
          <div className="intro-compare">
            {bench && (
              <div className="compare-card">
                <span className="compare-label">On foot</span>
                <strong>${bench.usd_per_km}<small> /km</small></strong>
              </div>
            )}
            <div className="compare-card compare-ours">
              <span className="compare-label">Fieldless</span>
              <strong>{dollars(scan.usd_per_km)}<small> /km</small></strong>
            </div>
          </div>
        )}
        {scan && <p className="overview-headline">{headline(scan, bench).line}</p>}
        {scan && bench && (
          <p className="intro-scope">
            On foot: {bench.name}: {bench.scope}. Fieldless: this example{name ? `, ${name}` : ""}. {bench.caveat}{" "}
            <a href={bench.source} target="_blank" rel="noreferrer">Source</a>
          </p>
        )}
        <div className="overview-actions">
          {scan && name && (
            <button type="button" className="primary" onClick={props.onSee}>See the finished survey of {name}</button>
          )}
          {canSurvey && <button type="button" onClick={props.onSurvey}>Survey your own streets</button>}
        </div>
      </div>
      {scan && (
        <>
          <ItemCards scan={scan} counts={props.counts} examples={props.examples} />
          <Flow scan={scan} issues={props.issues} />
          <Checks scan={scan} issues={props.issues} others={props.others} />
          {props.runs.length > 0 && <Runs runs={props.runs} onOpenRun={props.onOpenRun} />}
          <Surveys first={{ scan, name: name ?? `Survey ${scan.id}` }} others={props.others} onOpenScan={props.onOpenScan} />
          <p className="overview-foot">Built with Mapillary, SAM 3.1, Muse Spark, Muse Spark Open and Muse Glimmer</p>
        </>
      )}
    </section>
  );
}

// One card per item the survey found, all the same size, in the standard's order: one pass of the photos finds them all.
function ItemCards({ scan, counts, examples }: { scan: Scan; counts: Record<string, number>; examples: Example[] }) {
  const present = scan.items.filter((it) => (counts[it.id] ?? 0) > 0);
  return (
    <section className="overview-part" aria-labelledby="overview-items">
      <h3 id="overview-items">One pass, every item</h3>
      <ul className="overview-grid overview-items">
        {present.map((it) => {
          const label = itemName(it.id, scan.items);
          const photo = examples.find((e) => e.item === it.id);
          const city = cityLine(scan.city_check?.items[it.id], !!scan.city_check)?.text;
          return (
            <li key={it.id} className="overview-card overview-item">
              {photo ? (
                <img src={photo.url} alt={`${label} in a street photo`} loading="lazy"
                  style={{ objectPosition: thumbPosition(photo.box) }} />
              ) : <span className="overview-no-photo" aria-hidden="true" />}
              <span className="overview-item-name">
                <span className="dot" style={{ background: colourOf(it.id, scan.items) }} aria-hidden="true" />
                {label}
              </span>
              <span className="overview-item-count">{plural(counts[it.id], "finding")}</span>
              {city && <span className="overview-item-city">{city}</span>}
            </li>
          );
        })}
      </ul>
      <p className="hint small">Photos: Mapillary contributors, CC BY-SA 4.0</p>
    </section>
  );
}

// The flow as boxes: what goes in (the rules, the city's records, the photos), what the models do, the three ways a
// finding meets the city's list, and what goes back to the city. The outcome shapes are the map's city-records lens.
function Flow({ scan, issues }: { scan: Scan; issues: Issue[] }) {
  const city = scan.city_check;
  const totals = cityTotals(city, issues);
  const lists = city ? Object.values(city.items).map((c) => c.layer) : [];
  const fields = totals?.fields.slice(0, 3).join(", ") ?? "";
  return (
    <section className="overview-part" aria-labelledby="overview-flow">
      <h3 id="overview-flow">How the pieces fit</h3>
      <div className="flow">
        <ul className="flow-row">
          <li className="overview-card flow-box">
            <strong>Survey rules</strong>
            <span>{plural(scan.checks.length, "question")} in the {STANDARD[scan.standard]} standard</span>
          </li>
          {city && (
            <li className="overview-card flow-box">
              <strong>City records</strong>
              <span>{plural(lists.length, "city list")}</span>
              <span className="flow-lists">{lists.join(" · ")}</span>
            </li>
          )}
          <li className="overview-card flow-box">
            <strong>Street photos</strong>
            <span>{scan.image_count} public Mapillary photos</span>
          </li>
        </ul>
        <span className="flow-arrow" aria-hidden="true" />
        <p className="flow-models">
          SAM 3.1 finds each item · Muse Spark judges it and says why · Muse Spark Open double-checks possible defects
        </p>
        <span className="flow-arrow" aria-hidden="true" />
        {totals ? (
          <ul className="flow-row">
            <li className="overview-card flow-box flow-outcome">
              <span className="flow-shape flow-in" aria-hidden="true" />
              <strong className="flow-count">{totals.matched}</strong><span className="flow-label">Found and in the city's list</span>
            </li>
            <li className="overview-card flow-box flow-outcome">
              <span className="flow-shape flow-new" aria-hidden="true" />
              <strong className="flow-count">{totals.notInList}</strong><span className="flow-label">Found, not in the city's list</span>
            </li>
            <li className="overview-card flow-box flow-outcome">
              <span className="flow-shape flow-missed" aria-hidden="true" />
              <strong className="flow-count">{totals.missed}</strong><span className="flow-label">In the city's list, not found</span>
              <span className="hint small">a list to check</span>
            </li>
          </ul>
        ) : <p className="overview-card flow-none">This survey has no city list to compare.</p>}
        <span className="flow-arrow" aria-hidden="true" />
        <p className="flow-back">
          Back to the city: GeoJSON and a CSV per item in the city's own field names{fields ? ` (${fields})` : ""}, and a
          survey report.
        </p>
      </div>
    </section>
  );
}

interface Check {
  title: string;
  big: string | null;
  text: string;
  how?: string;
}

// What the numbers rest on, each a big number and one sentence; a card whose data this survey lacks is left out.
function Checks({ scan, issues, others }: { scan: Scan; issues: Issue[]; others: Props["others"] }) {
  const cards: Check[] = [];
  const totals = cityTotals(scan.city_check, issues);
  if (totals) {
    cards.push({ title: "City records", big: `${totals.matched} of ${totals.covered}`,
      text: "of the city's recorded assets in the photos' view were found" });
  }
  const n = secondOpinions(issues);
  const disagree = "when the two disagree the finding stays unclear";
  cards.push(n > 0
    ? { title: "Second model", big: String(n),
      text: `${n === 1 ? "possible defect" : "possible defects"} asked again by Muse Spark Open, an open-weight model; ${disagree}` }
    : { title: "Second model", big: null,
      text: `Muse Spark Open, an open-weight model, asks every possible defect again; ${disagree}` });
  const eye = CHECKED_BY_EYE[scan.id];
  if (eye) {
    cards.push({ title: "Checked by eye", big: `${eye.right} of ${eye.of}`,
      text: "findings the models kept were right when each photo was checked", how: eye.how });
  }
  const other = others.find((o) => o.scan.city_check);
  const list = other ? firstList(other.scan) : null;
  if (other && list) {
    cards.push({ title: "Another city", big: `${list.found} of ${list.covered}`, text: `${other.name}: ${list.layer}` });
  }
  return (
    <section className="overview-part" aria-labelledby="overview-checks">
      <h3 id="overview-checks">Checked, not assumed</h3>
      <ul className="overview-grid overview-checks">
        {cards.map((c) => (
          <li key={c.title} className="overview-card overview-check">
            <span className="overview-card-title">{c.title}</span>
            {c.big && <strong className="overview-big">{c.big}</strong>}
            <p>{c.text}</p>
            {c.how && <p className="hint small">{c.how}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

// The items Muse Spark wrote in the app for this survey: the SAM 3.1 phrases it tried on the photos another model
// labelled, the one it chose, what the item keeps, and what it found and cost.
function Runs({ runs, onOpenRun }: { runs: ItemRun[]; onOpenRun: Props["onOpenRun"] }) {
  return (
    <section className="overview-part" aria-labelledby="overview-runs">
      <h3 id="overview-runs">New items, written by Muse Spark</h3>
      <ul className="overview-grid overview-runs">
        {runs.slice(0, 2).map((run) => {
          const t = run.test;
          const tries = phraseJourney(run.progress);
          return (
            <li key={run.id} className="overview-card overview-run">
              <h4>{capital(run.target)}</h4>
              {t?.false && (
                <p>{`${t.judge ?? "Muse Spark Open"} saw it in ${t.found[1]} of ${t.found[1] + t.false[1]} photos`}</p>
              )}
              {tries.length > 0 && (
                <>
                  <span className="overview-card-title" id={`run-${run.id}-tries`}>Phrases SAM 3.1 tried</span>
                  <ol className="phrase-tries" aria-labelledby={`run-${run.id}-tries`}>
                    {tries.map((p, i) => {
                      const text = `${p.phrase} ${p.hit}/${p.of}`;
                      return p.phrase === t?.phrase
                        ? <li key={i} className="phrase-chosen"><strong>{text}</strong> chosen</li>
                        : <li key={i}>{text}</li>;
                    })}
                  </ol>
                </>
              )}
              {t?.kept && t.false && (
                <p>{`Kept ${t.kept[0]} of ${t.kept[1]} that show it, ${t.false[0]} of ${t.false[1]} without it`}</p>
              )}
              <p>{`${run.issues.length} found in this survey · ${dollars(run.cost_usd)}`}</p>
              <button type="button" onClick={() => onOpenRun(run.scan_id, run.id)}>See it on the map</button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// The example surveys side by side, each under its own country's standard; a card opens that survey.
function Surveys({ first, others, onOpenScan }: { first: Props["others"][number]; others: Props["others"];
  onOpenScan: Props["onOpenScan"] }) {
  return (
    <section className="overview-part" aria-labelledby="overview-cities">
      <h3 id="overview-cities">Two cities, two rulebooks</h3>
      <ul className="overview-grid overview-cities">
        {[first, ...others].map(({ scan, name }) => {
          const list = firstList(scan);
          return (
            <li key={scan.id}>
              <button type="button" className="overview-card overview-city" onClick={() => onOpenScan(scan.id)}>
                <span className="overview-city-name">{name}</span>
                <span>{STANDARD[scan.standard]} standard</span>
                {scan.usd_per_km !== null && <span>{dollars(scan.usd_per_km)} per km</span>}
                <span>{scan.image_count} photos</span>
                {list && <span>{`${list.found} of ${list.covered} in ${list.layer}`}</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
