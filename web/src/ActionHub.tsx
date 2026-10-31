import { useMemo, useState } from "react";
import { cityStates as computeCityStates, missedRecords, type CityState, type MissedRecord } from "./cityMatch";
import { photoMonth } from "./format";
import { colourOf, itemName, type Finding } from "./items";
import type { Lens } from "./LensSwitch";
import { thumbPosition } from "./summary";
import type { Scan } from "./types";

export type ActionTab = "dispatch" | "gis";

export interface DispatchRow {
  workOrderId: string;
  finding: Finding;
  priority: "P1 · Repair" | "P2 · Field verification";
  assetType: string;
  issueDescription: string;
  recommendedAction: string;
  street: string | null;
  photoDate: string | null;
  oldPhoto: boolean;
}

// The places a crew should go, from the survey's own findings: possible defects first, then what the models were
// unsure of, taking the items in turn so the top of the list is not one item (docs/ui-rules.md U2). Everything else
// needs no visit, which is the point of the list.
export function buildDispatchList(findings: Finding[], scan: Scan, streets: Record<string, string> = {}): DispatchRow[] {
  const rows: DispatchRow[] = [];
  const turn = new Map<DispatchRow, number>();
  const seen: Record<string, number> = {};
  let p1Count = 0;
  let p2Count = 0;

  for (const f of findings) {
    const repair = f.badge === "defect";
    if (!repair && f.badge !== "site" && f.status !== "field_check" && f.status !== "needs_review") continue;
    const count = repair ? ++p1Count : ++p2Count;
    const row: DispatchRow = {
      workOrderId: `WO-${scan.standard}-${scan.id}-${repair ? "P1" : "P2"}-${String(count).padStart(3, "0")}`,
      finding: f,
      priority: repair ? "P1 · Repair" : "P2 · Field verification",
      assetType: itemName(f.item, scan.items),
      issueDescription: f.title,
      recommendedAction: repair ? "Inspect and repair" : "Check on site",
      street: streets[f.key] ?? null,
      photoDate: f.picked.item.captured_at ?? f.picked.item.evidence?.captured_at ?? null,
      oldPhoto: f.picked.item.old_photo,
    };
    const group = `${row.priority} ${f.item}`;
    seen[group] = (seen[group] ?? 0) + 1;
    turn.set(row, seen[group]);
    rows.push(row);
  }

  return rows.sort((a, b) => a.priority.localeCompare(b.priority) || turn.get(a)! - turn.get(b)!);
}

export function workOrderCsv(rows: DispatchRow[]): string {
  // text that starts like a formula stays text in a spreadsheet: titles come from the models' answers
  const esc = (s: string) => `"${(/^[=+\-@\t\r]/.test(s) ? `'${s}` : s).replace(/"/g, '""')}"`;
  const header = "work_order_id,priority,asset_type,issue_description,street_address,latitude,longitude,recommended_action,status,photo_evidence_url";
  const lines = rows.map((r) => {
    const ev = r.finding.picked.item.evidence?.url ?? "";
    return [
      esc(r.workOrderId),
      esc(r.priority),
      esc(r.assetType),
      esc(r.issueDescription),
      esc(r.street ?? ""),
      r.finding.lat.toFixed(6),
      r.finding.lng.toFixed(6),
      esc(r.recommendedAction),
      esc(r.finding.status),
      esc(ev),
    ].join(",");
  });
  return [header, ...lines].join("\n");
}

export function gisDiffGeoJson(
  unrecorded: Finding[],
  missed: MissedRecord[],
  scan: Scan,
): string {
  const features = [
    ...unrecorded.map((f) => ({
      type: "Feature",
      properties: {
        diff_action: "add_to_city_db",
        category: itemName(f.item, scan.items),
        title: f.title,
        status: f.status,
        evidence_photo: f.picked.item.evidence?.url ?? null,
      },
      geometry: { type: "Point", coordinates: [f.lng, f.lat] },
    })),
    ...missed.map((r) => ({
      type: "Feature",
      properties: {
        diff_action: "verify_missing_city_record",
        category: itemName(r.item, scan.items),
        city_layer: r.layer,
        city_fields: Object.fromEntries(r.fields),
      },
      geometry: { type: "Point", coordinates: [r.lng, r.lat] },
    })),
  ];
  return JSON.stringify({ type: "FeatureCollection", features }, null, 2);
}

function downloadBlob(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

interface Props {
  scan: Scan;
  findings: Finding[];
  streets?: Record<string, string>;
  cityStates?: Record<string, CityState>;
  lens?: Lens;
  initialTab?: ActionTab;
  onPickFinding?: (f: Finding) => void;
  onPickRecord?: (r: MissedRecord) => void;
  onSwitchLens?: (l: Lens) => void;
}

export default function ActionHub({
  scan,
  findings,
  streets = {},
  cityStates,
  lens = "findings",
  initialTab = "dispatch",
  onPickFinding,
  onPickRecord,
  onSwitchLens,
}: Props) {
  const [tab, setTab] = useState<ActionTab>(initialTab);
  const [expanded, setExpanded] = useState(false);

  const dispatchRows = useMemo(() => buildDispatchList(findings, scan, streets), [findings, scan, streets]);

  const resolvedCityStates = useMemo(() => {
    if (cityStates) return cityStates;
    const issues = findings.flatMap((f) => (f.picked.kind === "issue" ? [f.picked.item] : []));
    return computeCityStates(scan.city_check, issues);
  }, [cityStates, scan.city_check, findings]);

  const missed = useMemo(() => missedRecords(scan.city_check), [scan.city_check]);
  const unrecorded = useMemo(
    () => findings.filter((f) => resolvedCityStates[f.key] === "not_in_list"),
    [findings, resolvedCityStates],
  );
  const matchedCount = useMemo(
    () => findings.filter((f) => resolvedCityStates[f.key] === "in_list").length,
    [findings, resolvedCityStates],
  );

  const shownDispatch = expanded ? dispatchRows : dispatchRows.slice(0, 3);
  const shownUnrecorded = expanded ? unrecorded : unrecorded.slice(0, 3);

  return (
    <div className="action-hub" role="region" aria-label="Actionable workflows from this survey">
      <div className="action-hub-head">
        <div className="action-hub-top-line">
          <span className="action-hub-eyebrow">What to do with this survey</span>
        </div>

        <div className="action-tabs" role="tablist" aria-label="Survey action tools">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "dispatch"}
            className={`action-tab${tab === "dispatch" ? " active" : ""}`}
            onClick={() => {
              setTab("dispatch");
              setExpanded(false);
            }}
          >
            Dispatch
            <span className="action-tab-badge">{dispatchRows.length}</span>
          </button>
          {scan.city_check && (
            <button
              type="button"
              role="tab"
              aria-selected={tab === "gis"}
              className={`action-tab${tab === "gis" ? " active" : ""}`}
              onClick={() => {
                setTab("gis");
                setExpanded(false);
              }}
            >
              GIS Diff
              <span className="action-tab-badge">+{unrecorded.length}</span>
            </button>
          )}
        </div>
      </div>

      {tab === "dispatch" && (
        <div className="action-pane">
          <p className="action-lead">
            Instead of walking all <strong>{scan.km !== null ? `${scan.km.toFixed(2)} km` : "this area"}</strong>, send a crew only to{" "}
            <strong>{dispatchRows.length}</strong> pinpointed locations needing repair or a site check:
          </p>

          {dispatchRows.length > 0 ? (
            <>
              <ul className="action-card-list">
                {shownDispatch.map((r) => {
                  const photo = r.finding.picked.item.evidence?.url;
                  const urgent = r.priority.startsWith("P1");
                  return (
                    <li key={r.finding.key} className="action-card-item">
                      <div
                        className="action-card"
                        onClick={() => onPickFinding?.(r.finding)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPickFinding?.(r.finding)}
                      >
                        {photo ? (
                          <div className="action-card-thumb-wrap">
                            <img
                              src={photo}
                              alt=""
                              className="action-card-thumb"
                              loading="lazy"
                              style={{ objectPosition: thumbPosition(r.finding.box) }}
                            />
                          </div>
                        ) : (
                          <div className="action-card-thumb-placeholder">
                            <span className="action-dot" style={{ background: colourOf(r.finding.item, scan.items) }} />
                          </div>
                        )}
                        <div className="action-card-body">
                          <div className="action-card-badge-line">
                            <span className={`action-prio-tag ${urgent ? "prio-p1" : "prio-p2"}`}>
                              {urgent ? "P1 Repair" : "P2 Site check"}
                            </span>
                            <span className="action-card-category">{r.assetType}</span>
                            {r.photoDate && (
                              <span
                                className={`action-freshness-tag${r.oldPhoto ? " stale" : ""}`}
                                title={r.oldPhoto ? "Photo over 3 years old" : "When the photo was taken"}
                              >
                                {photoMonth(r.photoDate)}
                              </span>
                            )}
                          </div>
                          <strong className="action-card-heading">{r.issueDescription}</strong>
                          <div className="action-card-street">
                            <span>{r.street || `${r.finding.lat.toFixed(4)}, ${r.finding.lng.toFixed(4)}`}</span>
                          </div>
                          <div className="action-card-action-guide">
                            <span className="guide-label">Action:</span>
                            <span className="guide-val">{r.recommendedAction}</span>
                          </div>
                          <div className="action-card-footer">
                            <span className="action-card-ticket">{r.workOrderId}</span>
                            <span className="action-card-jump">Locate on map &amp; photo ›</span>
                          </div>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <div className="action-foot">
                {dispatchRows.length > 3 && (
                  <button
                    type="button"
                    className="link-button action-more-btn"
                    onClick={() => setExpanded((v) => !v)}
                  >
                    {expanded ? "Show fewer" : `Show all ${dispatchRows.length} locations`}
                  </button>
                )}
                <button
                  type="button"
                  className="action-export-btn"
                  onClick={() =>
                    downloadBlob(
                      `scan_${scan.id}_work_order.csv`,
                      workOrderCsv(dispatchRows),
                      "text/csv;charset=utf-8",
                    )
                  }
                >
                  Work order CSV ({dispatchRows.length})
                </button>
              </div>
            </>
          ) : (
            <p className="hint small">No defects or site-check items flagged in this area.</p>
          )}
        </div>
      )}

      {tab === "gis" && scan.city_check && (
        <div className="action-pane">
          <p className="action-lead">
            Against {scan.city_check.city}'s records: <strong>{matchedCount}</strong> confirmed,{" "}
            <strong>+{unrecorded.length}</strong> found on the street but not listed,{" "}
            <strong>-{missed.length}</strong> listed but not seen in the photos.
          </p>
          <div className="gis-diff-pills">
            <button
              type="button"
              className="gis-pill gis-pill-add"
              onClick={() => onSwitchLens?.("records")}
            >
              <strong>+{unrecorded.length}</strong>
              <span>Unrecorded · Add to DB</span>
            </button>
            <button
              type="button"
              className="gis-pill gis-pill-miss"
              onClick={() => {
                onSwitchLens?.("records");
                if (missed[0]) onPickRecord?.(missed[0]);
              }}
            >
              <strong>-{missed.length}</strong>
              <span>Not seen · Check record</span>
            </button>
          </div>
          {unrecorded.length > 0 && (
            <ul className="action-card-list">
              {shownUnrecorded.map((f) => {
                const photo = f.picked.item.evidence?.url;
                const catName = itemName(f.item, scan.items);
                return (
                  <li key={f.key} className="action-card-item">
                    <div
                      className="action-card"
                      onClick={() => onPickFinding?.(f)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPickFinding?.(f)}
                    >
                      {photo ? (
                        <div className="action-card-thumb-wrap">
                          <img
                            src={photo}
                            alt=""
                            className="action-card-thumb"
                            loading="lazy"
                            style={{ objectPosition: thumbPosition(f.box) }}
                          />
                        </div>
                      ) : (
                        <div className="action-card-thumb-placeholder">
                          <span className="action-dot" style={{ background: colourOf(f.item, scan.items) }} />
                        </div>
                      )}
                      <div className="action-card-body">
                        <div className="action-card-badge-line">
                          <span className="action-prio-tag prio-gis">+ Unrecorded</span>
                          <span className="action-card-category">{catName}</span>
                        </div>
                        <strong className="action-card-heading">Found on the street · Not in the city's list</strong>
                        <div className="action-card-street">
                          <span>{streets[f.key] || `${f.lat.toFixed(4)}, ${f.lng.toFixed(4)}`}</span>
                        </div>
                        <div className="action-card-action-guide">
                          <span className="guide-label">Action:</span>
                          <span className="guide-val">Add this {catName} to the city's list</span>
                        </div>
                        <div className="action-card-footer">
                          <span className="action-card-ticket">GPS: {f.lat.toFixed(5)}, {f.lng.toFixed(5)}</span>
                          <span className="action-card-jump">View street photo ›</span>
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="action-foot">
            <button
              type="button"
              className="link-button action-more-btn"
              onClick={() => onSwitchLens?.(lens === "records" ? "findings" : "records")}
            >
              {lens === "records" ? "← Back to standard map" : "Highlight diff on map"}
            </button>
            <button
              type="button"
              className="action-export-btn"
              onClick={() =>
                downloadBlob(
                  `scan_${scan.id}_city_diff_patch.geojson`,
                  gisDiffGeoJson(unrecorded, missed, scan),
                  "application/geo+json;charset=utf-8",
                )
              }
            >
              GIS Diff Patch (.geojson)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
