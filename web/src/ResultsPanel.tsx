import { useEffect, useMemo, useRef, useState } from "react";
import { filesUrl, STATIC_DEMO } from "./api";
import { dollars, isFinished, photoMonth, yearsSpan } from "./format";
import { itemName, STATUS_SENTENCE, type Finding } from "./items";
import { itemArt } from "./markerArt";
import { cityLine, reviewChips } from "./summary";
import type { Scan } from "./types";

interface Props {
  scan: Scan;
  name: string | null; // the demo area's name, when it is one
  findings: Finding[];
  streets: Record<string, string>; // "11th Avenue, near East Pike Street" by finding key, from the map's road names
  selectedKey: string | null;
  onPick: (f: Finding) => void;
  onReview: () => void;
}

const STATUS_SHORT: Record<string, string> = {
  needs_review: "To review", field_check: "Check on site", verified: "Confirmed", auto: "",
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const SHORT_LIST = 5; // an item's list shows this many until "Show all" (a phone would scroll for metres)

// The results, top to bottom (docs/specs/2026-10-28-ui-redesign-design.md 3.1, docs/ui-rules.md U1, U2, U8): the
// survey, its three main numbers, a published walking survey for scale, one equal row per item, a person's review,
// the files, then each item's findings.
export default function ResultsPanel(props: Props) {
  const { scan, findings } = props;
  const items = scan.items;
  const art = useMemo(() => itemArt(items), [items]);
  const city = scan.city_check;
  const { counts } = scan;
  const present = items.filter((it) => findings.some((f) => f.item === it.id));
  const selectedRow = useRef<HTMLButtonElement>(null);
  const [opened, setOpened] = useState<Set<string>>(new Set());

  useEffect(() => {
    selectedRow.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [props.selectedKey]);

  return (
    <section className="panel-section results" aria-labelledby="results-title">
      <h2 id="results-title">{props.name ?? `Survey ${scan.id}`}</h2>
      <p className="sub">
        Survey {scan.id} · {scan.image_count} street photos · taken {yearsSpan(scan.captured_from, scan.captured_to)}
      </p>

      <div className="headline">
        <div>
          <strong>{scan.km !== null ? scan.km.toFixed(2) : "–"}<small> km</small></strong>
          <span>street surveyed</span>
        </div>
        <div>
          <strong>{((scan.processing_seconds + scan.review_seconds) / 60).toFixed(1)}<small> min</small></strong>
          <span>{(scan.processing_seconds / 60).toFixed(1)} processing + {(scan.review_seconds / 60).toFixed(1)} review</span>
        </div>
        <div>
          <strong>{dollars(scan.usd_per_km ?? scan.cost_usd)}<small>{scan.usd_per_km !== null ? " /km" : ""}</small></strong>
          <span>{dollars(scan.cost_usd)} in all</span>
        </div>
      </div>

      <div className="found-head">
        <h3>What the survey found</h3>
        {city && (
          <a className="small" href={city.method_url} target="_blank" rel="noreferrer"
            title={`Measured offline on ${city.measured_at} (${city.city})`}>How the city list check works</a>
        )}
      </div>
      <ul className="found">
        {present.map((it) => {
          const mine = findings.filter((f) => f.item === it.id);
          const confirmed = mine.filter((f) => f.badge === "defect" && f.status === "verified").length;
          const possible = mine.filter((f) => f.badge === "defect" && f.status !== "verified").length;
          const site = mine.filter((f) => f.badge === "site").length;
          const line = cityLine(city?.items[it.id], !!city);
          return (
            <li key={it.id}>
              <button type="button" className="found-row"
                onClick={() => document.getElementById(`list-${it.id}`)?.scrollIntoView({ behavior: "smooth" })}>
                <img src={art[it.id]} alt="" width={36} height={36} />
                <span className="found-name">{itemName(it.id, items)}</span>
                <span className="found-count">{mine.length}</span>
                <span className="found-notes">
                  {confirmed > 0 && <span className="chip chip-defect">{plural(confirmed, "defect")} confirmed</span>}
                  {possible > 0 && <span className="chip chip-defect">{plural(possible, "possible defect")}</span>}
                  {site > 0 && <span className="chip chip-site">{site} to check on site</span>}
                </span>
                {line && (
                  <span className="found-city">
                    {line.share !== null && (
                      <span className="city-bar" aria-hidden="true"><span style={{ width: `${Math.round(line.share * 100)}%` }} /></span>
                    )}
                    {line.text}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="review-chips">
        <strong>A person's review</strong>
        {reviewChips(counts).map((c) => (
          <span key={c.text} className={`chip${c.kind === "site" ? " chip-site" : ""}`}>{c.text}</span>
        ))}
      </p>
      {counts.needs_review > 0 && !STATIC_DEMO && (
        <button type="button" className="primary" onClick={props.onReview}>
          Review {plural(counts.needs_review, "finding")}
        </button>
      )}
      {isFinished(scan) && (
        <p className="download">
          <a className="button-link" href={filesUrl(scan.id)} download>Download survey files</a>
          <span className="hint small"> A one-page report, GeoJSON and a CSV per item for a GIS, and the evidence photos.</span>
        </p>
      )}

      {present.map((it) => {
        const all = findings.filter((f) => f.item === it.id);
        const at = all.findIndex((f) => f.key === props.selectedKey);
        const full = opened.has(it.id) || at >= SHORT_LIST;
        const mine = full ? all : all.slice(0, SHORT_LIST);
        return (
          <section key={it.id} className="item-section" aria-labelledby={`list-${it.id}`}>
            <h3 id={`list-${it.id}`} className="item-heading">
              <img src={art[it.id]} alt="" width={28} height={28} /> {itemName(it.id, items)} ({all.length})
            </h3>
            <ul className="finding-list">
              {mine.map((f, i) => {
                const evidence = f.picked.item.evidence;
                const street = props.streets[f.key];
                const status = STATUS_SHORT[f.status] ?? "";
                const selected = f.key === props.selectedKey;
                const when = f.picked.kind === "issue" && !f.picked.item.durable ? photoMonth(f.picked.item.captured_at) : "";
                return (
                  <li key={f.key}>
                    <button type="button" ref={selected ? selectedRow : undefined}
                      className={`finding${selected ? " finding-selected" : ""}`} aria-current={selected || undefined}
                      onClick={() => props.onPick(f)}
                      aria-label={`${f.title}${street ? `, ${street}` : ""}. ${STATUS_SENTENCE[f.status] ?? ""}`}>
                      {evidence ? <img className="thumb" src={evidence.url} alt="" loading="lazy" /> : <span className="thumb" />}
                      <span className="finding-text">
                        <span className="finding-title">{i + 1}. {f.title}</span>
                        <span className="finding-sub">{[street ?? "position from the photo", when].filter(Boolean).join(" · ")}</span>
                      </span>
                      {status && <span className={`chip chip-${f.badge ?? "none"}`}>{status}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
            {!full && all.length > SHORT_LIST && (
              <button type="button" className="link-button" onClick={() => setOpened((o) => new Set(o).add(it.id))}>
                Show all {all.length}
              </button>
            )}
          </section>
        );
      })}
    </section>
  );
}
