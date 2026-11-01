import { useEffect, useMemo, useState } from "react";
import { api, assetCheckReportUrl, assetCheckTableUrl } from "./api";
import type { MissedRecord } from "./cityMatch";
import { colourOf, type Finding } from "./items";
import type { Lens } from "./LensSwitch";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import type { AssetCheck, AssetCheckSummary, AssetRow, Scan } from "./types";

const STATUSES: { status: AssetRow["status"]; label: string; count: (s: AssetCheckSummary) => number }[] = [
  { status: "Seen in photo", label: "Seen in photo", count: (s) => s.totals.seen },
  { status: "Not determined", label: "Not determined", count: (s) => s.totals.not_determined },
  { status: "Not on the city list", label: "Not on the list", count: (s) => s.totals.not_listed },
];
const HOW_ORDER = ["Second look, panorama", "Second look", "Survey", ""];
const SHOWN = 3;
const HOW_WORDS: [string, string][] = [["Survey", "by the survey"], ["Second look", "on a second look"],
  ["Second look, panorama", "on a second look in panoramas"]];

// How the seen records came to be seen, so the count lines up with the survey's own.
export function seenHow(seenBy: Record<string, number>): string {
  return HOW_WORDS.filter(([how]) => seenBy[how]).map(([how, words]) => `${seenBy[how]} ${words}`).join(", ");
}

// The newest evidence first, taking the assets in turn so the top of the list is not one item (docs/ui-rules.md U2).
export function orderRows(rows: AssetRow[]): AssetRow[] {
  const turn = new Map<AssetRow, number>();
  const seen: Record<string, number> = {};
  for (const r of rows) {
    const group = `${r.how_seen} ${r.item}`;
    seen[group] = (seen[group] ?? 0) + 1;
    turn.set(r, seen[group]);
  }
  return [...rows].sort((a, b) => HOW_ORDER.indexOf(a.how_seen) - HOW_ORDER.indexOf(b.how_seen) || turn.get(a)! - turn.get(b)!);
}

// What a row is on the map: the survey's finding on the same photo, or the city record the survey did not find.
export function rowTarget(row: AssetRow, findings: Finding[], missed: MissedRecord[]):
  { finding: Finding } | { record: MissedRecord } | null {
  const url = row.evidence?.url;
  const finding = url ? findings.find((f) => f.item === row.item && f.picked.item.evidence?.url === url) : undefined;
  if (finding) return { finding };
  const record = missed.find((m) => m.item === row.item && m.fields.some(([, value]) => value === row.city_record));
  return record ? { record } : null;
}

export function AssetRowCard({ row, scan, onShow }: { row: AssetRow; scan: Scan; onShow?: () => void }) {
  const facts: [string, string][] = [
    ["City date", row.city_date],
    ["Seen in photo on", row.seen_in_photo_on],
    ["How seen", row.how_seen],
    ["Photo newer than city date", row.photo_newer_than_city_date],
    ["Models unsure", row.models_unsure],
  ];
  return (
    <li className="asset-row">
      {row.evidence && (
        <EvidencePhoto evidence={row.evidence} box={row.box} boxLabel={row.asset.toLowerCase()}
          colour={colourOf(row.item, scan.items)} alt={`Street photo: ${row.asset}`} />
      )}
      <div className="asset-row-body">
        <div className="action-card-badge-line">
          <span className="action-prio-tag prio-gis">{row.status}</span>
          <span className="action-card-category">{row.asset}</span>
        </div>
        <strong className="action-card-heading">{row.city_record || "No city record"}</strong>
        <p className="asset-row-place">{row.city_location || (row.city_record ? row.city_list : row.position_accuracy)}</p>
        <dl className="asset-row-facts">
          {facts.filter(([, value]) => value).map(([name, value]) => (
            <div key={name}><dt>{name}</dt><dd>{value}</dd></div>
          ))}
        </dl>
        {row.evidence && <PhotoCredit evidence={row.evidence} />}
        {row.how_seen === "Second look, panorama" && <p className="credit">A flat view cut from a 360-degree photo.</p>}
        {onShow && <button type="button" className="link-button action-more-btn" onClick={onShow}>Show on the map</button>}
      </div>
    </li>
  );
}

interface Props {
  scan: Scan;
  summary: AssetCheckSummary;
  findings: Finding[];
  missed: MissedRecord[];
  onPickFinding?: (f: Finding) => void;
  onPickRecord?: (r: MissedRecord) => void;
  onSwitchLens?: (l: Lens) => void;
}

// The city's records one by one, with what the photos settled (eval.asset_check). "Not determined" does not mean
// missing, and nothing here changes the city's records.
export default function AssetCheckPane({ scan, summary, findings, missed, onPickFinding, onPickRecord, onSwitchLens }: Props) {
  const [check, setCheck] = useState<AssetCheck | null>(null);
  const [failed, setFailed] = useState(false);
  const [status, setStatus] = useState<AssetRow["status"]>("Seen in photo");
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let live = true;
    setCheck(null);
    setFailed(false);
    api.assetCheck(scan.id).then((c) => live && setCheck(c), () => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [scan.id]);

  const rows = useMemo(() => orderRows((check?.rows ?? []).filter((r) => r.status === status)), [check, status]);
  const shown = expanded ? rows : rows.slice(0, SHOWN);
  const { totals } = summary;

  const show = (row: AssetRow) => {
    const target = rowTarget(row, findings, missed);
    if (!target) return undefined;
    return () => {
      if ("finding" in target) {
        onPickFinding?.(target.finding);
      } else {
        onSwitchLens?.("records");
        onPickRecord?.(target.record);
      }
    };
  };

  return (
    <div className="action-pane">
      <p className="action-lead">
        Of <strong>{totals.in_view}</strong> {summary.city} records in the photos' view, <strong>{totals.seen}</strong> were
        seen and <strong>{totals.not_determined}</strong> could not be settled. <strong>{totals.not_listed}</strong> more
        objects are not on the city's lists, and the models were unsure of {totals.not_listed_unsure} of those.
      </p>
      <p className="hint small">Seen: {seenHow(totals.seen_by)}.</p>
      <table className="asset-table">
        <thead>
          <tr><th scope="col">Asset</th><th scope="col">In view</th><th scope="col">Seen</th><th scope="col">Not determined</th>
            <th scope="col">Not on list</th></tr>
        </thead>
        <tbody>
          {summary.items.map((i) => (
            <tr key={i.item}>
              <th scope="row">{i.asset}</th><td>{i.in_view}</td><td>{i.seen}</td><td>{i.not_determined}</td><td>{i.not_listed}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="asset-filters" role="group" aria-label="Rows to show">
        {STATUSES.map((s) => (
          <button key={s.status} type="button" className="asset-filter" aria-pressed={status === s.status}
            onClick={() => {
              setStatus(s.status);
              setExpanded(false);
            }}>
            <strong>{s.count(summary)}</strong>
            <span>{s.label}</span>
          </button>
        ))}
      </div>
      {failed && <p className="hint small">The rows could not be loaded. The report and the table still have them.</p>}
      <ul className="asset-rows">
        {shown.map((r) => <AssetRowCard key={`${r.item} ${r.city_record} ${r.evidence?.url ?? ""} ${r.lat}`} row={r} scan={scan} onShow={show(r)} />)}
      </ul>
      {status === "Not determined" && <p className="hint small">Not determined does not mean missing. These need a newer photo or a site visit.</p>}
      <div className="action-foot asset-foot">
        {rows.length > SHOWN && (
          <button type="button" className="link-button action-more-btn" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Show fewer" : `Show all ${rows.length}`}
          </button>
        )}
        <a className="action-export-btn" href={assetCheckReportUrl(scan.id)} target="_blank" rel="noreferrer">Open the report</a>
        <a className="action-export-btn" href={assetCheckTableUrl(scan.id, "csv")} download>Table (.csv)</a>
        <a className="action-export-btn" href={assetCheckTableUrl(scan.id, "geojson")} download>Table (.geojson)</a>
      </div>
    </div>
  );
}
