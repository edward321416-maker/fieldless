import type { CityCheck, Issue } from "./types";

// The survey against the city's own records (eval/city_check.py, the scan's city_check), for the overview's three
// numbers and the map's city-records lens. Per item with a city list:
// - matched: the city's records in a photo's view that a finding was paired with (`found`)
// - notInList: findings not rejected by a person and paired with no city record (not in `matched_ids`)
// - missed: the city's records in a photo's view that no finding was paired with (`covered` - `found`)
export interface CityItemCounts {
  matched: number;
  notInList: number;
  missed: number;
  covered: number;
}

export type CityState = "in_list" | "not_in_list";

export interface MissedRecord {
  key: string; // "record:<item>:<n>", unique on the map
  item: string;
  layer: string; // the city's name for the list, e.g. "SDOT street trees"
  lat: number;
  lng: number;
  fields: [string, string][]; // the fields the city names the record by, in the city's order, as text
}

const kept = (issues: Issue[], item: string) =>
  issues.filter((i) => i.issue_type === item && i.review_status !== "rejected");

export function cityCounts(city: CityCheck | undefined, issues: Issue[]): Record<string, CityItemCounts> {
  if (!city) return {};
  return Object.fromEntries(Object.entries(city.items).map(([item, c]) => {
    const matched = new Set<number | string>(c.matched_ids);
    const notInList = kept(issues, item).filter((i) => !matched.has(i.id)).length;
    return [item, { matched: c.found, notInList, missed: Math.max(0, c.covered - c.found), covered: c.covered }];
  }));
}

// The items added up, and the field names the city keeps a record by (first item's first, no repeats).
export function cityTotals(city: CityCheck | undefined, issues: Issue[]):
  (CityItemCounts & { fields: string[] }) | null {
  if (!city) return null;
  const sum = { matched: 0, notInList: 0, missed: 0, covered: 0 };
  for (const c of Object.values(cityCounts(city, issues))) {
    sum.matched += c.matched;
    sum.notInList += c.notInList;
    sum.missed += c.missed;
    sum.covered += c.covered;
  }
  const fields = [...new Set(Object.values(city.items).flatMap((c) => c.id_fields ?? []))];
  return { ...sum, fields };
}

// Each finding of an item the city keeps a list of, by the finding's key (items.ts keyOf): paired with a city
// record or not. Findings of other items, and rejected ones, have no state.
export function cityStates(city: CityCheck | undefined, issues: Issue[]): Record<string, CityState> {
  if (!city) return {};
  const out: Record<string, CityState> = {};
  for (const [item, c] of Object.entries(city.items)) {
    const matched = new Set<number | string>(c.matched_ids);
    for (const i of kept(issues, item)) out[`issue:${i.id}`] = matched.has(i.id) ? "in_list" : "not_in_list";
  }
  return out;
}

export function missedRecords(city: CityCheck | undefined): MissedRecord[] {
  if (!city) return [];
  return Object.entries(city.items).flatMap(([item, c]) => (c.missed ?? []).map((m, n) => ({
    key: `record:${item}:${n}`, item, layer: c.layer, lat: m.lat, lng: m.lng,
    fields: (c.id_fields ?? Object.keys(m.city)).map((f): [string, string] => [f, String(m.city[f] ?? "")]),
  })));
}
