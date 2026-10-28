import { BENCHMARKS } from "./benchmarks";
import { dollars } from "./format";
import { itemName, type Finding } from "./items";
import { PhotoCredit } from "./Photo";
import { thumbPosition } from "./summary";
import type { ItemMeta, Scan } from "./types";

export interface Example {
  item: string;
  url: string; // the evidence photo
  box: number[] | null;
}

interface Props {
  scan: Scan | null; // the first example survey, loaded behind the introduction
  name: string | null; // its area's name
  examples: Example[]; // one photo per item, from that survey's findings
  canSurvey: boolean; // false on the static demo: no new surveys there
  onSee: () => void;
  onSurvey: () => void;
}

// What a first visit sees (docs/specs/2026-10-29-cleanup-design.md 4): what Fieldless does, its cost per km beside a
// published walking survey's, one photo per item the example found (Project Sidewalk shows an example per label),
// and where to go next.
export default function IntroPanel({ scan, name, examples, canSurvey, onSee, onSurvey }: Props) {
  const bench = scan ? BENCHMARKS[scan.standard] : undefined;
  return (
    <section className="intro" aria-labelledby="intro-title">
      <h2 id="intro-title">Street surveys without the fieldwork</h2>
      <p className="intro-lead">
        Cities send people to walk every block and write down what is on the street. Fieldless reads the public street
        photos that already exist and maps the same things, each with its photo.
      </p>
      {scan && bench && scan.usd_per_km !== null && (
        <>
          <div className="intro-compare">
            <div className="compare-card">
              <span className="compare-label">On foot</span>
              <strong>${bench.usd_per_km}<small> /km</small></strong>
            </div>
            <div className="compare-card compare-ours">
              <span className="compare-label">Fieldless</span>
              <strong>{dollars(scan.usd_per_km)}<small> /km</small></strong>
            </div>
          </div>
          <p className="intro-scope">
            On foot: {bench.name}: {bench.scope}. Fieldless: this example{name ? `, ${name}` : ""}. {bench.caveat}{" "}
            <a href={bench.source} target="_blank" rel="noreferrer">Source</a>
          </p>
        </>
      )}
      {scan && examples.length > 0 && (
        <div className="intro-items">
          <span className="compare-label">What the example found, each with its photo</span>
          <ul className="intro-photos">
            {examples.map((e) => {
              const label = itemName(e.item, scan.items);
              return (
                <li key={e.item}>
                  <img src={e.url} alt={`${label} in a street photo`} loading="lazy"
                    style={{ objectPosition: thumbPosition(e.box) }} />
                  <span>{label}</span>
                </li>
              );
            })}
          </ul>
          <span className="hint small">Photos: Mapillary contributors, CC BY-SA 4.0</span>
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

// One of the example's photos over the introduction's map, with the model's box, what found and checked it, and
// the photo's credit (after Blyncsy, which shows a real detection before its map).
export function IntroPhoto({ finding, items, photos }: { finding: Finding; items: ItemMeta[]; photos: number }) {
  const evidence = finding.picked.item.evidence;
  const box = finding.box;
  if (!evidence || !box || box.length < 4) return null;
  const target = items.find((it) => it.id === finding.item)?.target ?? finding.item.replace(/_/g, " ");
  const model = finding.picked.kind === "issue" ? finding.picked.item.check?.model.split(" (")[0] : null;
  return (
    <figure className="intro-photo">
      <div className="intro-photo-frame">
        <img src={evidence.url} alt={`Street photo: ${finding.title}`} />
        <span className="photo-box" style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`,
          width: `${(box[2] - box[0]) / 10}%`, height: `${(box[3] - box[1]) / 10}%` }} />
      </div>
      <figcaption>
        One of this example's {photos} photos: SAM 3.1 found the {target}{model ? `; ${model} checked it` : ""}.
        <PhotoCredit evidence={evidence} />
      </figcaption>
    </figure>
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
