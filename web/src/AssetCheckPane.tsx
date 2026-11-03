import { useContext, useEffect, useMemo, useState } from "react";
import { api, assetCheckReportUrl, assetCheckTableUrl } from "./api";
import type { MissedRecord } from "./cityMatch";
import { count, dollars, photoMonth } from "./format";
import { colourOf, type Finding } from "./items";
import type { Lens } from "./LensSwitch";
import LedgerReviewPane, { initialReviewState } from "./LedgerReviewPane";
import { LedgerReviewContext } from "./LedgerReviewContext";
import { reviewSignature } from "./ledgerReview";
import { EvidencePhoto, MapillaryMark, PhotoCredit } from "./Photo";
import type { AssetCheck, AssetCheckSummary, AssetRow, NearbyPhoto, Scan } from "./types";

const ACTIONS = ["Correct the record", "Update the date", "No change", "Visit on site", "Compare with the list"];
const NO_VISIT = ["Correct the record", "Update the date", "No change"];
const VISIT = "Visit on site";
const COMPARE = "Compare with the list";
// The list opens on rows of several assets: a correction may be one row of one asset (docs/ui-rules.md U2).
const FIRST = "Update the date";
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

// What a person can look at before going to a place the photos did not settle. It is linked, so it shows on Mapillary
// with its credit there; here it says what it is, and that it may not show the record.
export function NearbyPhotoLink({ photo }: { photo: NearbyPhoto }) {
  return (
    <div className="nearby-photo">
      <a href={photo.link} target="_blank" rel="noreferrer">
        See a survey photo taken {Math.max(1, Math.round(photo.dist_m))} m from the record's position
      </a>
      <p className="hint small">It may not show the record.</p>
      <p className="credit">
        Photo by {photo.creator ?? "a Mapillary contributor"}, {photoMonth(photo.captured_at)} ·{" "}
        <a href={photo.link} target="_blank" rel="noreferrer"><MapillaryMark alt="" /> View on Mapillary</a>
        {" "}· <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">{photo.license}</a>
      </p>
    </div>
  );
}

export function AssetRowCard({ row, scan, onShow }: { row: AssetRow; scan: Scan; onShow?: () => void }) {
  const open = row.attribute_state === "unsettled" || row.attribute_state === "not_checked";
  const facts: [string, string][] = [
    ["City says", row.city_says],
    ["Photo shows", row.photo_shows],
    ["Status", row.status],
    ["City date", row.city_date],
    ["Seen in photo on", row.seen_in_photo_on],
    ["How seen", row.how_seen],
    ["Photo newer than city date", row.photo_newer_than_city_date],
    ["Models unsure", row.models_unsure],
    // an object seen is not an attribute checked: which field is still open, and what the row would be if it agreed
    ["Attribute", row.attribute_field && row.attribute_state ? `${row.attribute_field}: ${row.attribute_state.replace("_", " ")}. ${row.attribute_note ?? ""}`.trim() : ""],
    ["If the attribute agreed", open ? row.action_if_attribute_agrees ?? "" : ""],
    ["Why a visit", row.visit_reason ?? ""],
  ];
  return (
    <li className="asset-row">
      {row.evidence && (
        <EvidencePhoto evidence={row.evidence} box={row.box} boxLabel={row.asset.toLowerCase()}
          colour={colourOf(row.item, scan.items)} alt={`Street photo: ${row.asset}`} />
      )}
      <div className="asset-row-body">
        <div className="action-card-badge-line">
          <span className="action-prio-tag prio-gis">{row.action}</span>
          <span className="action-card-category">{row.asset}</span>
        </div>
        <strong className="action-card-heading">{row.city_record || "No city record"}</strong>
        <p className="asset-row-place">{row.city_location || (row.city_record ? row.city_list : row.position_accuracy)}</p>
        <dl className="asset-row-facts">
          {facts.filter(([, value]) => value).map(([name, value]) => (
            <div key={name}><dt>{name}</dt><dd>{value}</dd></div>
          ))}
        </dl>
        {row.nearby_photo && <NearbyPhotoLink photo={row.nearby_photo} />}
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

// The Record Update List: each row with what the city could do with it (eval.asset_check). "Not determined" does not
// mean missing, and nothing here changes the city's records.
export default function AssetCheckPane({ scan, summary, findings, missed, onPickFinding, onPickRecord, onSwitchLens }: Props) {
  const ledger = useContext(LedgerReviewContext);
  const [check, setCheck] = useState<AssetCheck | null>(null);
  const [failed, setFailed] = useState(false);
  const present = ACTIONS.filter((a) => summary.actions[a]);
  const [action, setAction] = useState(present.includes(FIRST) ? FIRST : present[0]);
  const [expanded, setExpanded] = useState(false);
  const [reviewing, setReviewing] = useState(ledger?.session?.scanId === scan.id && ledger.session.open);

  useEffect(() => {
    let live = true;
    setCheck(null);
    setFailed(false);
    setReviewing(ledger?.session?.scanId === scan.id && ledger.session.open);
    api.assetCheck(scan.id).then((c) => live && setCheck(c), () => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [scan.id]);

  const rows = useMemo(() => orderRows((check?.rows ?? []).filter((r) => r.action === action)), [check, action]);
  const shown = expanded ? rows : rows.slice(0, SHOWN);
  const { totals } = summary;
  const visits = totals.in_view - summary.no_visit;
  const objects = summary.actions[COMPARE] ?? 0;
  const secondLooks = summary.trust.second_looks_usd;
  const checked = summary.trust.findings;
  const findingsChecked = checked
    ? `An AI assistant checked ${checked.n} findings on their photos: ${checked.yes} right, ${checked.no} wrong, ${checked.unclear} could not be told. This is a prototype check by an AI assistant, not an independent evaluation.`
    : null;
  const attributes = summary.attribute_checks;
  const attributesOpen = attributes ? attributes.unsettled + attributes.not_checked : 0;

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
        {totals.in_area !== undefined
          ? <>Of <strong>{count(totals.in_area)}</strong> {summary.city} records in the survey's box or its photos' view, the photos reached{" "}
            <strong>{totals.in_view}</strong>: </>
          : <>Of <strong>{totals.in_view}</strong> {summary.city} records in the photos' view, </>}
        <strong>{summary.no_visit}</strong> {summary.no_visit === 1 ? "is a candidate" : "are candidates"} for desk review and <strong>{visits}</strong>{" "}
        {visits === 1 ? "needs" : "need"} a newer photo or a field check.
        {objects > 0 && <> <strong>{objects}</strong> more objects are not on the city's lists{summary.compare_unsure > 0 ? `, ${summary.compare_unsure} of them not settled` : ""}.</>}
      </p>
      <p className="hint small">
        Seen: {seenHow(totals.seen_by)}. Every row is a suggestion. Nothing here changes the city's records. Candidates for desk review are
        not a measured count of avoided site visits.
        {findingsChecked && <> {findingsChecked}</>}
      </p>
      {attributes && (
        <p className="hint small">
          Seen in a photo is not the same as checked: {attributesOpen} of the {attributes.required} seen records with an attribute to check have it open
          ({attributes.unsettled} unsettled and {attributes.not_checked} not checked). Each is a visit row that says which attribute and why.
        </p>
      )}
      {secondLooks !== undefined && (
        <p className="hint small">The survey cost {dollars(scan.cost_usd)}. The second looks behind this list, run separately, cost {dollars(secondLooks)} more.</p>
      )}
      {onSwitchLens && <button type="button" className="link-button" onClick={() => onSwitchLens("update")}>See the visits on the map</button>}
      {summary.credit && <p className="hint small">{summary.credit}</p>}
      <table className="asset-table">
        <thead>
          <tr><th scope="col">Asset</th><th scope="col">In view</th><th scope="col">Desk review</th><th scope="col">Photo or field check</th>
            <th scope="col">Compare</th></tr>
        </thead>
        <tbody>
          {summary.items.map((i) => (
            <tr key={i.item}>
              <th scope="row">{i.asset}</th><td>{i.in_view}</td>
              <td>{NO_VISIT.reduce((n, a) => n + (i.actions[a] ?? 0), 0)}</td>
              <td>{i.actions[VISIT] ?? 0}</td><td>{i.actions[COMPARE] ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="asset-filters" role="group" aria-label="Rows to show">
        {present.map((a) => (
          <button key={a} type="button" className="asset-filter" aria-pressed={action === a}
            onClick={() => {
              setAction(a);
              setExpanded(false);
            }}>
            <strong>{summary.actions[a]}</strong>
            <span>{a}</span>
          </button>
        ))}
      </div>
      <div className="action-foot asset-foot">
        <a className="action-export-btn" href={assetCheckReportUrl(scan.id)} target="_blank" rel="noreferrer">Open the report</a>
        <a className="action-export-btn" href={assetCheckTableUrl(scan.id, "csv")} download>Table (.csv)</a>
        <a className="action-export-btn" href={assetCheckTableUrl(scan.id, "geojson")} download>Table (.geojson)</a>
      </div>
      <div className="ledger-entry">
        <button type="button" className="action-export-btn" aria-expanded={reviewing} disabled={!check}
          onClick={() => {
            const next = !reviewing;
            setReviewing(next);
            if (ledger && check) {
              const signature = reviewSignature(scan.id, check.rows);
              ledger.setSession((previous) => ({ scanId: scan.id, signature, open: next,
                state: previous?.signature === signature ? previous.state : initialReviewState() }));
            }
          }}>Review for ledger</button>
        <p className="hint small">Check each record against its photo and prepare change files for the city's ledger. Nothing is applied here.</p>
      </div>
      {reviewing && check && <LedgerReviewPane scan={scan} city={summary.city} check={check} showFor={show} />}
      {failed && <p className="hint small">The rows could not be loaded. The report and the table still have them.</p>}
      <ul className="asset-rows" hidden={reviewing}>
        {shown.map((r) => <AssetRowCard key={`${r.item} ${r.city_record} ${r.evidence?.url ?? ""} ${r.lat}`} row={r} scan={scan} onShow={show(r)} />)}
      </ul>
      {action === VISIT && <p className="hint small">Not determined does not mean missing. These need a newer photo or a field check.</p>}
      {!reviewing && rows.length > SHOWN && (
        <button type="button" className="link-button action-more-btn" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}
