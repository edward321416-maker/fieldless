import type { Bbox, DemoArea } from "./types";

interface Props {
  coverage: boolean; // the map draws Mapillary's coverage (a server that passes the tiles on)
  demos: DemoArea[];
  onGo: (bbox: Bbox) => void;
}

// Over the map while a new survey is open (docs/specs/2026-10-28-design-pass2-design.md 2): where photos already
// exist, and example areas to go to. It never draws the box itself; the person does.
export default function CoverageGuide({ coverage, demos, onGo }: Props) {
  return (
    <section className="coverage-guide" aria-label="Where street photos exist">
      <h2>Street photos that already exist</h2>
      {coverage ? (
        <>
          <p>Green lines are walks people have already photographed and shared on Mapillary. Draw a box over them: Fieldless surveys those photos, and no one walks the street again.</p>
          <p className="coverage-key"><span aria-hidden="true" /> Street photo walks · Mapillary</p>
        </>
      ) : (
        <p>Draw a box over streets that people have photographed on Mapillary: Fieldless surveys those photos, and no one walks the street again.</p>
      )}
      {demos.length > 0 && (
        <p className="coverage-try">Try:{" "}
          {demos.map((d) => <button key={d.scan_id} type="button" onClick={() => onGo(d.bbox)}>{d.name}</button>)}
        </p>
      )}
    </section>
  );
}
