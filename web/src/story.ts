import type { Fact } from "./benchmarks";
import type { MissedRecord } from "./cityMatch";
import { count, dollars } from "./format";
import { actionLabel } from "./reviewLabels";
import type { AssetCheckSummary, AssetRow, NearbyPhoto, Scan, UnreachedEntry } from "./types";

// The overview's story, in the order a visitor asks: the problem, how the models solve it, whether it holds up, what a
// person can do with it, what changes (docs/specs/2026-11-02-app-story-design.md). Every number about a survey comes
// from the server; published ones are in benchmarks.ts and measured ones in claims.ts.
const VISIT = "Visit on site";
const COMPARE = "Compare with the list";
const NEW_DATE = "Update the date";

export const MODEL_STEPS = [
  { model: "Muse Spark", does: "watches each walk", text: "It reads the photos as a short video and names what to look for." },
  { model: "SAM 3.1", does: "finds each item by name", text: "A phrase is the prompt, and it marks the object in the photo." },
  { model: "Muse Spark", does: "answers the item's question", text: "With a box and a reason, or unclear when the photo can't settle it." },
  { model: "Muse Spark Open", does: "asks again", text: "An open-weight second opinion on possible defects. When the two disagree the finding stays unclear." },
];

// The records the photos reached that are not candidates for desk review. no_visit is the old key of that count.
export const visitsLeft = (a: AssetCheckSummary) => a.totals.in_view - a.no_visit;

// How the survey's 27 of 94 becomes the update list's 43 of 94, so the two numbers do not read as a contradiction.
export function recordFunnel(a: AssetCheckSummary): { n: number; label: string }[] {
  const area = a.totals.in_area;
  return [
    ...(area !== undefined ? [{ n: area, label: "city records in the survey's box or its photos' view" }] : []),
    { n: a.totals.in_view, label: area !== undefined ? "in the photos' view" : "city records in the photos' view" },
    { n: a.totals.seen_by.Survey ?? 0, label: "found by the survey" },
    { n: a.totals.seen, label: "seen, with second looks" },
    { n: a.no_visit, label: "candidates for desk review" },
  ];
}

export function visitLine(a: AssetCheckSummary & { quality_gate?: { visit_causes?: { primary?: Record<string, number> } } }): string {
  const visits = visitsLeft(a);
  const primary = a.quality_gate?.visit_causes?.primary;
  const wording: Record<string, string> = {
    not_determined: "the photos could not settle",
    models_unsure: "were seen but not sure enough",
    identity_unresolved: "need a desk check of the facility's identity",
    audit_hold: "have photo details held as unclear by an AI visual audit",
    attribute_open: "were seen but a field the city keeps for them is still open",
  };
  if (primary && Object.entries(primary).every(([k, v]) => Object.prototype.hasOwnProperty.call(wording, k) && Number.isInteger(v) && v >= 0)
    && Object.values(primary).reduce((sum, n) => sum + n, 0) === visits) {
    const parts = Object.entries(wording).filter(([k]) => (primary[k] ?? 0) > 0).map(([k, words]) => `${primary[k]} ${words}`);
    if (parts.length) {
      const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
      return `The other ${visits} need a newer photo or a field check: ${list}.`;
    }
  }
  const open = a.visits_attribute_unresolved ?? 0;
  const unsure = visits - a.totals.not_determined - open;
  const parts = [`${a.totals.not_determined} the photos could not settle`,
    ...(open > 0 ? [`${open} were seen but a field the city keeps for them is still open`] : []),
    ...(unsure > 0 ? [`${unsure} were seen but not sure enough`] : [])];
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `The other ${visits} need a newer photo or a field check: ${list}.`;
}

// The records in the box no photo reached: the survey says nothing about them, and the page says so.
export function reachLine(a: AssetCheckSummary): string | null {
  const area = a.totals.in_area;
  if (area === undefined) return null;
  return `The other ${count(area - a.totals.in_view)} records in the box had no survey photo in view. This survey says nothing about them.`;
}

export function reachCheck(a: AssetCheckSummary): Fact | null {
  const area = a.totals.in_area;
  if (area === undefined) return null;
  return { title: "Reach", big: `${a.totals.in_view} of ${count(area)}`,
    text: "city records in the survey's box or its photos' view that a survey photo had in view. The survey says nothing about the rest." };
}

export function compareLine(a: AssetCheckSummary): string | null {
  const objects = a.actions[COMPARE] ?? 0;
  if (objects === 0) return null;
  return `${objects} more objects are not on the city's lists${a.compare_unsure > 0 ? `, ${a.compare_unsure} of them not settled` : ""}.`;
}

// Spots with no city record: how often both models still said an object was there.
export function controlCheck(a: AssetCheckSummary): Fact | null {
  const [first, ...rest] = a.trust.controls;
  if (!first) return null;
  return {
    title: "Control spots",
    big: `${first.both_yes} of ${first.asked}`,
    text: `spots with no city record where both models saw an object, in ${first.photos.toLowerCase()}`,
    how: rest.map((c) => `${c.both_yes} of ${c.asked} in ${c.photos.toLowerCase()}`).join(" · ") || undefined,
  };
}

// The example with the smallest share of candidates for desk review: the page shows where it does worst.
export function hardestExample(others: { scan: Scan; name: string }[]): { name: string; check: AssetCheckSummary } | null {
  const share = (a: AssetCheckSummary) => a.no_visit / (a.totals.in_view || 1);
  const [worst] = others.flatMap(({ scan, name }) => (scan.asset_check ? [{ name, check: scan.asset_check }] : []))
    .sort((a, b) => share(a.check) - share(b.check));
  return worst ?? null;
}

export function photoSpan(months: string[]): string | null {
  if (months.length === 0) return null;
  const [first, last] = [months[0], months[months.length - 1]];
  return first === last ? first : `${first} to ${last}`;
}

// Who is better off and how: each card says what a person knows before going out, from the survey's own numbers.
export function changeCards(check: AssetCheckSummary | undefined, name: string | null,
  run?: { target: string; cost_usd: number; survey: string } | null): Fact[] {
  const cards: Fact[] = [];
  const where = name ?? "this survey";
  if (check) {
    const visits = visitsLeft(check);
    const photos = check.visits_with_photo === visits && visits > 0;
    cards.push({ title: "For the person walking", big: `${visits} of ${check.totals.in_view}`,
      text: `records the photos reached in ${where} still need a newer photo or a field check${photos ? ", each with a survey photo, of the place or from nearby. See what the place looks like before you leave" : ""}. Every row is a suggestion.` });
    const dated = check.actions[NEW_DATE] ?? 0;
    if (check.date_gap_years !== undefined && dated > 0) {
      cards.push({ title: "For the person keeping the records", big: `${check.date_gap_years} years`,
        text: `median gap between the city's date and a newer photo, on ${dated} records in ${where}. Review each from a desk; the city decides.` });
    }
  }
  if (run) {
    cards.push({ title: "For the next question", big: dollars(run.cost_usd),
      text: `Muse Spark added "${run.target}" to a finished survey of ${run.survey}. Nobody walked the street again.` });
  }
  return cards;
}

// A city record in the box no photo reached, as a record's card shows it: its list and its ids, nothing else.
export function unreachedRecord(entry: UnreachedEntry, index: number, fields: Record<string, string[]>,
  layer: string): MissedRecord {
  const [item, lng, lat, ids] = entry;
  return { key: `unreached:${item}:${index}`, item, layer, lat, lng,
    fields: (fields[item] ?? []).map((name, k): [string, string] => [name, ids?.[k] ?? ""]) };
}

// The update list's row for a city record, and the photo near it.
export function rowOf(rows: AssetRow[], record: MissedRecord): AssetRow | undefined {
  return rows.find((r) => r.item === record.item && record.fields.some(([, value]) => value === r.city_record));
}

export function nearbyOf(rows: AssetRow[], record: MissedRecord): NearbyPhoto | undefined {
  return rowOf(rows, record)?.nearby_photo;
}

const SEEN_HOW: Record<string, string> = { Survey: "by the survey", "Second look": "on a second look",
  "Second look, panorama": "on a second look in a panorama" };
const SETTLED = ["Correct the record", "Update the date", "No change"];

// What a city record's card says: what its update list row says when a photo settled it, else that it needs a look.
export function recordLine(row: AssetRow | undefined): string {
  if (row && SETTLED.includes(row.action) && row.seen_in_photo_on) {
    const how = SEEN_HOW[row.how_seen] ? `, ${SEEN_HOW[row.how_seen]}` : "";
    return `Seen in a photo of ${row.seen_in_photo_on}${how}; the survey itself did not find it. Suggestion: ${actionLabel(row.action)}.`;
  }
  // there in a photo, but the city's yes or no for it is open: the row says which and why
  if (row?.status === "Seen in photo" && row.visit_reason && row.seen_in_photo_on) {
    return `Seen in a photo of ${row.seen_in_photo_on}; the survey itself did not find it. ${row.visit_reason}`;
  }
  return "In the city's list and in a photo's view; not found by this survey. Check on site or in the photos.";
}

export interface UpdateDot {
  key: string;
  item: string;
  lng: number;
  lat: number;
  visit: boolean;
  tip: string;
}

// A visit dot that is there in a photo says which of the city's fields is still open: seen is not checked.
const openAttribute = (r: AssetRow) => r.action === VISIT && r.attribute_field && (r.attribute_state === "unsettled" || r.attribute_state === "not_checked")
  ? ` · ${r.attribute_field} ${r.attribute_state === "unsettled" ? "not settled" : "not checked"}` : "";

export const dotKey = (row: AssetRow) => `${row.item} ${row.city_record}`;

// The update list on the map: the city's records only, so the dots add up to the list's sentence.
export function updateDots(rows: AssetRow[]): UpdateDot[] {
  return rows.filter((r) => r.city_record).map((r) => ({
    key: dotKey(r), item: r.item, lng: r.lng, lat: r.lat, visit: r.action === VISIT,
    tip: `${r.asset} ${r.city_record} · ${r.action}${openAttribute(r)}`,
  }));
}
