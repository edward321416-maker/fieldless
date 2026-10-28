import { BENCHMARKS } from "./benchmarks";
import { dollars } from "./format";
import { itemName } from "./items";
import type { Scan } from "./types";

interface Props {
  scan: Scan | null; // the first example survey, loaded behind the introduction
  name: string | null; // its area's name
  art: Record<string, string>; // each item's marker image (markerArt.itemArt), passed in so this renders anywhere
  canSurvey: boolean; // false on the static demo: no new surveys there
  onSee: () => void;
  onSurvey: () => void;
}

const STEPS = [
  "Choose streets people have already photographed on Mapillary",
  "SAM 3.1 finds each survey item by name; Muse Spark checks it and says why",
  "A person confirms only what the models are unsure of",
  "The survey leaves as files for the city's GIS",
];

// What a first visit sees (docs/specs/2026-10-29-first-screen-design.md 1): what Fieldless does, its cost per km
// beside a published walking survey's, the four steps, the items one pass records, and where to go next.
export default function IntroPanel({ scan, name, art, canSurvey, onSee, onSurvey }: Props) {
  const bench = scan ? BENCHMARKS[scan.standard] : undefined;
  const totalMin = scan ? Math.round((scan.processing_seconds + scan.review_seconds) / 60) : 0;
  const reviewMin = scan && scan.review_seconds > 0 ? Math.max(1, Math.round(scan.review_seconds / 60)) : 0;
  return (
    <section className="intro" aria-labelledby="intro-title">
      <h2 id="intro-title">Survey a city's streets from photos that already exist</h2>
      <p className="intro-lead">
        Cities still send people to walk every block and write down what is on the street. Fieldless does that first
        pass from public street photos, and a person only confirms.
      </p>
      {scan && bench && scan.usd_per_km !== null && scan.km !== null && (
        <>
          <div className="intro-compare">
            <div className="compare-card">
              <span className="compare-label">On foot</span>
              <strong>${bench.usd_per_km}<small> /km</small></strong>
              <span>{bench.people}</span>
              <span className="compare-source">{bench.name}</span>
            </div>
            <div className="compare-card compare-ours">
              <span className="compare-label">Fieldless</span>
              <strong>{dollars(scan.usd_per_km)}<small> /km</small></strong>
              <span>
                {totalMin} min for {scan.km.toFixed(2)} km{reviewMin > 0 && `, ${reviewMin} min of it a person's review`}
              </span>
              <span className="compare-source">This example{name ? `, ${name}` : ""}</span>
            </div>
          </div>
          <p className="intro-scope">
            {bench.caveat} <a href={bench.source} target="_blank" rel="noreferrer" title={bench.scope}>Source</a>
          </p>
        </>
      )}
      <ol className="intro-steps">
        {STEPS.map((s, i) => <li key={s}><span className="intro-n" aria-hidden="true">{i + 1}</span>{s}</li>)}
      </ol>
      {scan && scan.items.length > 0 && (
        <div className="intro-items">
          <span className="compare-label">One pass records</span>
          <p className="chips">
            {scan.items.map((it) => (
              <span key={it.id} className="chip item-chip">
                <img src={art[it.id]} alt="" width={20} height={20} /> {itemName(it.id, scan.items)}
              </span>
            ))}
          </p>
        </div>
      )}
      <div className="intro-actions">
        {scan && name && (
          <button type="button" className="primary" onClick={onSee}>See the finished survey of {name}</button>
        )}
        {canSurvey && <button type="button" onClick={onSurvey}>Survey your own streets</button>}
        <p className="hint small">Built with Mapillary, SAM 3.1, Muse Spark, Muse Spark Open and Muse Glimmer</p>
      </div>
    </section>
  );
}

// The key to the introduction's map: what its two kinds of line mean.
export function IntroMapKey({ coverage }: { coverage: boolean }) {
  return (
    <div className="intro-key" aria-label="Map key">
      {coverage && <span><span className="key-line key-coverage" aria-hidden="true" />Street photos already on Mapillary</span>}
      <span><span className="key-line key-walk" aria-hidden="true" />The walk this example surveyed</span>
    </div>
  );
}
