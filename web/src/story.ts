import type { Fact } from "./benchmarks";
import type { MissedRecord } from "./cityMatch";
import { count, dollars } from "./format";
import type { AssetCheckSummary, AssetRow, NearbyPhoto, Scan } from "./types";

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

export const visitsLeft = (a: AssetCheckSummary) => a.totals.in_view - a.no_visit;

// How the survey's 27 of 94 becomes the update list's 43 of 94, so the two numbers do not read as a contradiction.
export function recordFunnel(a: AssetCheckSummary): { n: number; label: string }[] {
  const area = a.totals.in_area;
  return [
    ...(area !== undefined ? [{ n: area, label: "city records in the survey's box or its photos' view" }] : []),
    { n: a.totals.in_view, label: area !== undefined ? "in the photos' view" : "city records in the photos' view" },
    { n: a.totals.seen_by.Survey ?? 0, label: "found by the survey" },
    { n: a.totals.seen, label: "seen, with second looks" },
    { n: a.no_visit, label: "need no site visit" },
  ];
}

export function visitLine(a: AssetCheckSummary): string {
  const visits = visitsLeft(a);
  const unsure = visits - a.totals.not_determined;
  return `The other ${visits} need a visit: ${a.totals.not_determined} the photos could not settle${unsure > 0 ? ` and ${unsure} were seen but not sure enough` : ""}.`;
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

// The example with the smallest share that needs no visit: the page shows where it does worst.
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
      text: `records the photos reached in ${where} still need a visit${photos ? ", each with a survey photo, of the place or from nearby. See what the place looks like before you leave" : ""}. Every row is a suggestion.` });
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
    return `Seen in a photo of ${row.seen_in_photo_on}${how}; the survey itself did not find it. Suggestion: ${row.action}.`;
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

export const dotKey = (row: AssetRow) => `${row.item} ${row.city_record}`;

// The update list on the map: the city's records only, so the dots add up to the list's sentence.
export function updateDots(rows: AssetRow[]): UpdateDot[] {
  return rows.filter((r) => r.city_record).map((r) => ({
    key: dotKey(r), item: r.item, lng: r.lng, lat: r.lat, visit: r.action === VISIT,
    tip: `${r.asset} ${r.city_record} · ${r.action}`,
  }));
}
