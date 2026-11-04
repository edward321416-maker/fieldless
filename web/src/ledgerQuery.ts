// Which rows the reviewer sees, and in what order. Pure functions: filtering and ordering only decide what is shown. A
// decision belongs to a row's review key, so nothing here can move a decision to another facility.
import { sourceOf } from "./ledgerReview";
import type { AssetRow, LedgerDecision } from "./types";

export const FILTERS = ["all", "candidates", "changes", "identity", "unclear", "hold", "unreviewed"] as const;
export type QueueFilter = (typeof FILTERS)[number];

export const FILTER_LABELS: Record<QueueFilter, string> = {
  all: "All", candidates: "Candidates", changes: "Corrections", identity: "Identity doubt", unclear: "Unclear",
  hold: "On hold", unreviewed: "Not reviewed",
};

// The actions of rows a desk review can settle (docs/ui-rules.md; the same three AssetCheckPane counts as "Desk review").
export const CANDIDATE_ACTIONS = ["Correct the record", "Update the date", "No change"];
const CHANGE_ACTION = "Correct the record";

export interface QueueSettings {
  query: string;
  item: string; // "" is every item
  filter: QueueFilter;
  action: string; // "" is the whole list
}

export const DEFAULT_QUEUE: QueueSettings = { query: "", item: "", filter: "all", action: "" };
export const QUERY_MAX = 200;

const squash = (text: string) => text.toLowerCase().replace(/\s+/g, " ").trim();

// Everything a person may type to find a row: its ID, its place, the other records it could be confused with.
export function searchText(row: AssetRow): string {
  const s = sourceOf(row);
  return squash([s?.record_id, row.city_record, row.city_location, row.city_list, row.asset, row.item, ...(row.competing_record_ids ?? [])]
    .filter((v): v is string => typeof v === "string").join(" "));
}

const hasIdentityDoubt = (r: AssetRow) => (sourceOf(r)?.identity_warnings.length ?? 0) > 0 || r.identity_state === "unresolved"
  || (r.competing_record_ids?.length ?? 0) > 0;
const attributeOpen = (r: AssetRow) => r.attribute_state === "unsettled" || r.attribute_state === "not_checked";
const hasClarityDoubt = (r: AssetRow) => r.evidence_quality === "unclear" || attributeOpen(r) || Boolean(r.models_unsure);

// 0 a correction, 1 identity or attribute doubt, 2 another candidate, 3 the rest. "not_flagged" and "not_reviewed" are no doubt.
export function priorityOf(row: AssetRow): 0 | 1 | 2 | 3 {
  if (row.action === CHANGE_ACTION) return 0;
  if (hasIdentityDoubt(row) || attributeOpen(row) || row.evidence_quality === "unclear") return 1;
  if (CANDIDATE_ACTIONS.includes(row.action)) return 2;
  return 3;
}

const keyOf = (r: AssetRow) => sourceOf(r)!.review_key;

function matches(row: AssetRow, filter: QueueFilter, decisions: Record<string, LedgerDecision>): boolean {
  switch (filter) {
    case "all": return true;
    case "candidates": return CANDIDATE_ACTIONS.includes(row.action);
    case "changes": return row.action === CHANGE_ACTION;
    case "identity": return hasIdentityDoubt(row);
    case "unclear": return hasClarityDoubt(row);
    case "hold": return Object.prototype.hasOwnProperty.call(decisions, keyOf(row)) && decisions[keyOf(row)].decision === "hold";
    case "unreviewed": return !Object.prototype.hasOwnProperty.call(decisions, keyOf(row));
  }
}

// The rows to show, in order. `rows` are the reviewable rows of the list. A search looks at the whole list: an action that was
// chosen elsewhere never hides the record a person typed.
export function queueRows(rows: AssetRow[], decisions: Record<string, LedgerDecision>, settings: QueueSettings): AssetRow[] {
  const query = squash(settings.query.slice(0, QUERY_MAX));
  const picked = rows.map((row, at) => ({ row, at })).filter(({ row }) => {
    if (settings.item && row.item !== settings.item) return false;
    if (!query && settings.action && row.action !== settings.action) return false;
    if (query && !searchText(row).includes(query)) return false;
    return matches(row, settings.filter, decisions);
  });
  const exact = (row: AssetRow) => Boolean(query) && squash(sourceOf(row)?.record_id ?? "") === query;
  picked.sort((a, b) => Number(exact(b.row)) - Number(exact(a.row)) || priorityOf(a.row) - priorityOf(b.row) || a.at - b.at);
  return picked.map((p) => p.row);
}

// How many rows each state holds, for the controls. Unaffected by the search, the item or the action.
export function filterCounts(rows: AssetRow[], decisions: Record<string, LedgerDecision>): Record<QueueFilter, number> {
  return Object.fromEntries(FILTERS.map((f) => [f, rows.filter((r) => matches(r, f, decisions)).length])) as Record<QueueFilter, number>;
}

export function itemsOf(rows: AssetRow[]): { item: string; asset: string }[] {
  const seen = new Map<string, string>();
  for (const r of rows) if (!seen.has(r.item)) seen.set(r.item, r.asset);
  return [...seen].map(([item, asset]) => ({ item, asset }));
}

export const isFilter = (v: unknown): v is QueueFilter => typeof v === "string" && (FILTERS as readonly string[]).includes(v);
