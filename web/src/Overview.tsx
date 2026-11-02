import type { ActionTab } from "./ActionHub";
import { BENCHMARKS, PROBLEM, type Fact } from "./benchmarks";
import { CHECKED_BY_EYE, GLIMMER_NOTE } from "./claims";
import { cityTotals } from "./cityMatch";
import { dollars } from "./format";
import { capital, colourOf, itemName } from "./items";
import { phraseJourney, secondOpinions } from "./overviewData";
import { MiniCredit } from "./Photo";
import { changeCards, compareLine, controlCheck, hardestExample, MODEL_STEPS, photoSpan, recordFunnel, visitLine } from "./story";
import { cityLine, headline, thumbPosition } from "./summary";
import type { AssetCheckSummary, Evidence, Issue, ItemRun, Scan } from "./types";

export interface Example {
  item: string;
  url: string; // the evidence photo
  box: number[] | null;
  evidence: Evidence; // its creator, Mapillary link and licence: shown under the photo
}

interface Props {
  scan: Scan | null; // the first example survey
  name: string | null; // its area's name
  issues: Issue[]; // its findings (not the agent items')
  counts: Record<string, number>; // findings per item id, entrances included
  examples: Example[]; // one photo per item
  runs: ItemRun[]; // the example surveys' newest finished item runs, at most two
  others: { scan: Scan; name: string }[]; // the other example surveys
  canSurvey: boolean; // false on the static demo
  onSee: () => void;
  onSurvey: () => void;
  onOpenScan: (id: number) => void;
  onOpenRun: (scanId: number, runId: number) => void;
  onLaunchAction?: (tab: ActionTab | "custom") => void;
}

const STANDARD: Record<Scan["standard"], string> = { US: "US", KR: "Korean" };
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A survey's first city list, the one its cards name ("6 of 23 in" the list's own name).
const firstList = (scan: Scan) => (scan.city_check ? Object.values(scan.city_check.items)[0] ?? null : null);

// A list sent as counts only (Seoul's, KOGL Type 4): said wherever its counts are, with the source it is credited to.
const countsOnlyNote = (scan: Scan) =>
  scan.city_check?.counts_only ? `City list: counts only. Source: ${scan.city_check.source}.` : null;

// What a first visit sees: the cost per km first, then the story in the order a reader asks it: the problem, how the
// models solve it, whether it holds up, what a person can do with it and what changes, then the example surveys
// (docs/specs/2026-11-02-app-story-design.md). Each number about a survey is the server's.
export default function Overview(props: Props) {
  const { scan, name, canSurvey } = props;
  const bench = scan ? BENCHMARKS[scan.standard] : undefined;
  const heroEx = props.examples[0];
  const heroItemLabel = scan && heroEx ? itemName(heroEx.item, scan.items) : null;
  const heroColour = scan && heroEx ? colourOf(heroEx.item, scan.items) : "#1d4ed8";
  const totalFindings = Object.values(props.counts).reduce((a, b) => a + b, 0);
  const totals = scan ? cityTotals(scan.city_check, props.issues) : null;
  const launchAction = (tab: ActionTab | "custom") => {
    if (props.onLaunchAction) props.onLaunchAction(tab);
    else props.onSee();
  };

  return (
    <section className="overview" aria-labelledby="overview-title">
      <div className="overview-hero">
        <div className="overview-opening">
          <span className="overview-kicker">AI street survey from public photos</span>
          <h2 id="overview-title">Street surveys without the fieldwork</h2>
          <p className="overview-lead">
            Cities send people to walk every block and write down what is on the street. Fieldless reads the public street
            photos that already exist and maps what a photo can show, each finding with its photo.
          </p>
          {scan && scan.usd_per_km !== null && (
            <div className="intro-compare">
              {bench && (
                <div className="compare-card">
                  <span className="compare-label">On foot, up to</span>
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
              {scan.asset_check?.trust.second_looks_usd !== undefined && " Update list second looks cost more; see its report."}
            </p>
          )}
          <div className="overview-actions">
            {scan && name && (
              <button type="button" className="primary launch-cta" onClick={props.onSee}>See the finished survey of {name}</button>
            )}
            {canSurvey && <button type="button" className="secondary-cta" onClick={props.onSurvey}>Survey your own streets</button>}
          </div>
        </div>

        {scan && (
          <div className="overview-showcase" onClick={props.onSee} role="presentation">
            <div className="showcase-window">
              <div className="showcase-bar">
                <span className="showcase-dots"><i /><i /><i /></span>
                <span className="showcase-title">Example survey · {name ?? `Survey ${scan.id}`}</span>
                <span className="showcase-launch-tag">Click to launch app →</span>
              </div>
              <div className="showcase-body">
                {heroEx && (
                  <div className="showcase-photo-wrap">
                    <img src={heroEx.url} alt="" />
                    {heroEx.box && heroEx.box.length === 4 && (
                      <div
                        className="showcase-bbox"
                        style={{
                          left: `${heroEx.box[0] / 10}%`,
                          top: `${heroEx.box[1] / 10}%`,
                          width: `${(heroEx.box[2] - heroEx.box[0]) / 10}%`,
                          height: `${(heroEx.box[3] - heroEx.box[1]) / 10}%`,
                          borderColor: heroColour,
                        }}
                      >
                        <span className="showcase-bbox-tag" style={{ background: heroColour }}>
                          {heroItemLabel}
                        </span>
                      </div>
                    )}
                    <div className="showcase-photo-caption">
                      <span>Found by SAM 3.1, judged by Muse Spark</span>
                      <span>{scan.image_count} street photos analyzed</span>
                    </div>
                  </div>
                )}
                {heroEx && <MiniCredit evidence={heroEx.evidence} />}
                <div className="showcase-stats-strip">
                  <div className="showcase-stat">
                    <strong>{totalFindings}</strong>
                    <span>Mapped findings</span>
                  </div>
                  <div className="showcase-stat">
                    <strong>{scan.items.length}</strong>
                    <span>Survey items</span>
                  </div>
                  <div className="showcase-stat">
                    <strong>{scan.usd_per_km !== null ? dollars(scan.usd_per_km) : "—"}/km</strong>
                    <span>Model cost</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
      {scan && (
        <>
          <Problem />
          <section className="overview-part" aria-labelledby="overview-flow">
            <StoryHead n={2} label="The solution" id="overview-flow" title="A survey item is a sentence"
              lead="SAM 3.1 finds things by name and Muse Spark checks them by question, so a new item needs no labelled photos and no trained model." />
            <Flow scan={scan} />
            <ItemCards scan={scan} counts={props.counts} examples={props.examples} />
            {props.runs.length > 0 && (
              <Runs runs={props.runs} surveys={[{ scan, name: name ?? `Survey ${scan.id}` }, ...props.others]} onOpenRun={props.onOpenRun} />
            )}
            <p className="flow-back story-back">{GLIMMER_NOTE}</p>
          </section>
          <Checks scan={scan} issues={props.issues} others={props.others} />
          <section className="overview-part overview-workflows" aria-labelledby="overview-do">
            <StoryHead n={4} label="What you can do" id="overview-do" title="Turn a survey into field work and city records" />
            <div className="workflow-grid">
              {scan.asset_check ? (
                <div
                  role="link"
                  tabIndex={0}
                  className="overview-card workflow-card"
                  onClick={() => launchAction("dispatch")}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && launchAction("dispatch")}
                >
                  <span className="workflow-badge prio-gis">City records</span>
                  <strong>{`${scan.asset_check.no_visit} of ${scan.asset_check.totals.in_view} city records need no site visit`}</strong>
                  <p>A list for the city's asset team: what to correct, which dates to update, where to visit and what to compare with the list, each row with its photo, as a page, a CSV and GeoJSON.</p>
                  <span className="workflow-cta">Open the update list →</span>
                </div>
              ) : (
                <>
              <div
                role="link"
                tabIndex={0}
                className="overview-card workflow-card"
                onClick={() => launchAction("dispatch")}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && launchAction("dispatch")}
              >
                <span className="workflow-badge prio-p1">Field check</span>
                <strong>A list of what to check on site</strong>
                <p>Possible defects and what the models were unsure of, each with its place, street photo and a CSV.</p>
                <span className="workflow-cta">Open Dispatch →</span>
              </div>
              {totals && countsOnlyNote(scan) ? (
                <div className="overview-card workflow-card">
                  <span className="workflow-badge prio-gis">City records</span>
                  <strong>{`${totals.matched} of ${totals.covered} of the city's records in the photos' view found`}</strong>
                  <p>{countsOnlyNote(scan)}</p>
                </div>
              ) : (
                <div
                  role="link"
                  tabIndex={0}
                  className="overview-card workflow-card"
                  onClick={() => launchAction("gis")}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && launchAction("gis")}
                >
                  <span className="workflow-badge prio-gis">City records</span>
                  <strong>{totals ? `${totals.notInList} not on the city's list · ${totals.missed} not found, to check` : "Compare with the city's list"}</strong>
                  <p>Compare the survey with the city's records and download the differences as GeoJSON, a list to check.</p>
                  <span className="workflow-cta">Open GIS Diff →</span>
                </div>
              )}
                </>
              )}
              <div
                role="link"
                tabIndex={0}
                className="overview-card workflow-card"
                onClick={() => launchAction("custom")}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && launchAction("custom")}
              >
                <span className="workflow-badge prio-ai">On-demand AI agent</span>
                <strong>Add a survey item</strong>
                <p>Type a street object, such as a fire hydrant or a bike rack. Muse Spark writes the item, tests it and surveys the same photos. Making one on the live site needs an invite code.</p>
                <span className="workflow-cta">Add a survey item →</span>
              </div>
              {canSurvey && (
                <div
                  role="link"
                  tabIndex={0}
                  className="overview-card workflow-card"
                  onClick={props.onSurvey}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && props.onSurvey()}
                >
                  <span className="workflow-badge prio-p2">New survey</span>
                  <strong>Survey your own streets</strong>
                  <p>Pick a place, see the photo count and the estimated cost, then start. Spending is capped.</p>
                  <span className="workflow-cta">Start a new survey →</span>
                </div>
              )}
            </div>
            <BackToCity scan={scan} issues={props.issues} />
          </section>
          <section className="overview-part" aria-labelledby="overview-change">
            <StoryHead n={5} label="What changes" id="overview-change" title="The walk goes where it's needed" />
            <FactCards facts={changeCards(scan, name)} />
          </section>
          {props.others.length > 0 && (
            <Surveys first={{ scan, name: name ?? `Survey ${scan.id}` }} others={props.others} onOpenScan={props.onOpenScan} />
          )}
          <p className="overview-foot">Built with Mapillary, SAM 3.1, Muse Spark, Muse Spark Open and Muse Glimmer</p>
        </>
      )}
    </section>
  );
}

// A part of the story: its number and name, its heading and a line under it.
function StoryHead({ n, label, id, title, lead }: { n: number; label: string; id: string; title: string; lead?: string }) {
  return (
    <div className="story-head">
      <span className="overview-kicker">{n} · {label}</span>
      <h3 id={id}>{title}</h3>
      {lead && <p className="hint">{lead}</p>}
    </div>
  );
}

function FactCards({ facts }: { facts: Fact[] }) {
  return (
    <ul className="overview-grid overview-checks">
      {facts.map((f) => (
        <li key={f.title} className="overview-card overview-check">
          <span className="overview-card-title">{f.title}</span>
          <strong className="overview-big">{f.big}</strong>
          <p>{f.text}</p>
          {f.how && <p className="hint small">{f.how}</p>}
          {f.source && <a className="overview-source" href={f.source} target="_blank" rel="noreferrer">Source</a>}
        </li>
      ))}
    </ul>
  );
}

function Problem() {
  return (
    <section className="overview-part" aria-labelledby="overview-problem">
      <StoryHead n={1} label="The problem" id="overview-problem" title="Keeping street records right means walking the streets" />
      <FactCards facts={PROBLEM} />
    </section>
  );
}

// One card per item the survey found, all the same size, in the standard's order: one pass of the photos finds them all.
function ItemCards({ scan, counts, examples }: { scan: Scan; counts: Record<string, number>; examples: Example[] }) {
  const present = scan.items.filter((it) => (counts[it.id] ?? 0) > 0);
  return (
    <section className="overview-part" aria-labelledby="overview-items">
      <h4 id="overview-items">One pass, every item</h4>
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
              {photo && <MiniCredit evidence={photo.evidence} />}
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
    </section>
  );
}

// What goes in (the rules, the city's records, the photos) and what each model does with it.
function Flow({ scan }: { scan: Scan }) {
  const city = scan.city_check;
  const lists = city ? Object.values(city.items).map((c) => c.layer) : [];
  const countsOnly = countsOnlyNote(scan);
  return (
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
            {countsOnly && <span className="hint small">{countsOnly}</span>}
          </li>
        )}
        <li className="overview-card flow-box">
          <strong>Street photos</strong>
          <span>{scan.image_count} public Mapillary photos</span>
        </li>
      </ul>
      <span className="flow-arrow" aria-hidden="true" />
      <ol className="flow-steps">
        {MODEL_STEPS.map((s) => (
          <li key={s.does} className="overview-card flow-box flow-step">
            <strong>{s.model}</strong>
            <span className="flow-label">{s.does}</span>
            <span>{s.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// A survey without an update list: the three ways a finding meets the city's list. The shapes are the map's lens.
function Outcomes({ scan, issues }: { scan: Scan; issues: Issue[] }) {
  const totals = cityTotals(scan.city_check, issues);
  if (!totals) return <p className="overview-card flow-none story-gap">This survey has no city list to compare.</p>;
  return (
    <ul className="flow-row story-gap">
      <li className="overview-card flow-box flow-outcome">
        <span className="flow-shape flow-in" aria-hidden="true" />
        <strong className="flow-count">{totals.matched}</strong><span className="flow-label">Found and in the city's list</span>
      </li>
      {totals.notInList !== null && (
        <li className="overview-card flow-box flow-outcome">
          <span className="flow-shape flow-new" aria-hidden="true" />
          <strong className="flow-count">{totals.notInList}</strong><span className="flow-label">Found, not in the city's list</span>
        </li>
      )}
      <li className="overview-card flow-box flow-outcome">
        <span className="flow-shape flow-missed" aria-hidden="true" />
        <strong className="flow-count">{totals.missed}</strong><span className="flow-label">In the city's list, not found</span>
        {!countsOnlyNote(scan) && <span className="hint small">a list to check</span>}
      </li>
    </ul>
  );
}

// From the city's records in view to those that need no visit, so the survey's own count and the list's read as one.
function RecordFunnel({ check }: { check: AssetCheckSummary }) {
  return (
    <div className="story-gap">
      <ol className="funnel">
        {recordFunnel(check).map((s) => (
          <li key={s.label} className="overview-card flow-box flow-outcome">
            <strong className="flow-count">{s.n}</strong><span className="flow-label">{s.label}</span>
          </li>
        ))}
      </ol>
      <p className="hint">{visitLine(check)} Not settled does not mean missing. {compareLine(check)}</p>
    </div>
  );
}

function BackToCity({ scan, issues }: { scan: Scan; issues: Issue[] }) {
  const fields = cityTotals(scan.city_check, issues)?.fields.slice(0, 3).join(", ") ?? "";
  return (
    <p className="flow-back story-back">
      Back to the city: GeoJSON and a CSV per item{countsOnlyNote(scan) ? "" : " in the city's own field names"}
      {fields ? ` (${fields})` : ""}, and a survey report.
    </p>
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
  const check = scan.asset_check;
  const totals = cityTotals(scan.city_check, issues);
  if (totals && !check) {
    cards.push({ title: "City records", big: `${totals.matched} of ${totals.covered}`,
      text: "of the city's recorded assets in the photos' view were found", how: countsOnlyNote(scan) ?? undefined });
  }
  const n = secondOpinions(issues);
  const disagree = "when the two disagree the finding stays unclear";
  cards.push(n > 0
    ? { title: "Second model", big: String(n),
      text: `${n === 1 ? "possible defect" : "possible defects"} asked again by Muse Spark Open, an open-weight model; ${disagree}` }
    : { title: "Second model", big: null,
      text: `Muse Spark Open, an open-weight model, asks possible defects on US survey items again; ${disagree}` });
  const eye = CHECKED_BY_EYE[scan.id];
  if (eye) {
    cards.push({ title: "Checked photo by photo", big: `${eye.right} of ${eye.of}`,
      text: "findings the models kept were right when each photo was checked", how: eye.how });
  }
  const control = check ? controlCheck(check) : null;
  if (control) cards.push(control);
  const hard = hardestExample(others);
  const other = others.find((o) => o.scan.city_check);
  const list = other ? firstList(other.scan) : null;
  if (hard) {
    const span = photoSpan(hard.check.trust.photo_months);
    cards.push({ title: "Hardest example", big: `${hard.check.no_visit} of ${hard.check.totals.in_view}`,
      text: `${hard.name}: city records that need no site visit${span ? `, photos of ${span}` : ""}` });
  } else if (other && list) {
    cards.push({ title: "Another example", big: `${list.found} of ${list.covered}`, text: `${other.name}: ${list.layer}`,
      how: countsOnlyNote(other.scan) ?? undefined });
  }
  return (
    <section className="overview-part" aria-labelledby="overview-checks">
      <StoryHead n={3} label="Does it hold up?" id="overview-checks" title="Checked, not assumed" />
      {check ? <RecordFunnel check={check} /> : <Outcomes scan={scan} issues={issues} />}
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
function Runs({ runs, surveys, onOpenRun }: { runs: ItemRun[]; surveys: Props["others"]; onOpenRun: Props["onOpenRun"] }) {
  const where = (id: number) => surveys.find((s) => s.scan.id === id)?.name ?? "this survey";
  return (
    <section className="overview-part" aria-labelledby="overview-runs">
      <h4 id="overview-runs">New items, written by Muse Spark</h4>
      <ul className="overview-grid overview-runs">
        {runs.slice(0, 2).map((run) => {
          const t = run.test;
          const tries = phraseJourney(run.progress);
          return (
            <li key={run.id} className="overview-card overview-run">
              <h5>{capital(run.target)}</h5>
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
              <p>{`${run.issues.length} found in ${where(run.scan_id)} · ${dollars(run.cost_usd)}`}</p>
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
      <h3 id="overview-cities">Example surveys</h3>
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
                {list && countsOnlyNote(scan) && <span className="hint small">{countsOnlyNote(scan)}</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
