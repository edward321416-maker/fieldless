import { REPAIR_BUDGET, type Fact } from "./benchmarks";
import { dollars } from "./format";
import type { AssetCheckSummary, AssetRow, Scan } from "./types";

// The overview's story, in the order a visitor asks: the problem, how the models solve it, whether it holds up, what a
// person can do with it, what changes (docs/specs/2026-11-02-app-story-design.md). Every number about a survey comes
// from the server; published ones are in benchmarks.ts and measured ones in claims.ts.
const VISIT = "Visit on site";
const COMPARE = "Compare with the list";

export const MODEL_STEPS = [
  { model: "Muse Spark", does: "watches each walk", text: "It reads the photos as a short video and names what to look for." },
  { model: "SAM 3.1", does: "finds each item by name", text: "A phrase is the prompt, and it marks the object in the photo." },
  { model: "Muse Spark", does: "answers the item's question", text: "With a box and a reason, or unclear when the photo can't settle it." },
  { model: "Muse Spark Open", does: "asks again", text: "An open-weight second opinion on possible defects. When the two disagree the finding stays unclear." },
];

export const visitsLeft = (a: AssetCheckSummary) => a.totals.in_view - a.no_visit;

// How the survey's 27 of 94 becomes the update list's 43 of 94, so the two numbers do not read as a contradiction.
export function recordFunnel(a: AssetCheckSummary): { n: number; label: string }[] {
  return [
    { n: a.totals.in_view, label: "city records in the photos' view" },
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

export function changeCards(scan: Pick<Scan, "asset_check" | "usd_per_km">, name: string | null): Fact[] {
  const cards: Fact[] = [];
  const a = scan.asset_check;
  if (a) {
    cards.push({ title: "For the person walking", big: `${visitsLeft(a)} of ${a.totals.in_view}`,
      text: `places still need a visit in ${name ?? "this survey"}. The day starts from those. Every row is a suggestion.` });
  }
  if (scan.usd_per_km !== null) {
    cards.push({ title: "For the city's records", big: `${dollars(scan.usd_per_km)} /km`,
      text: "to survey again wherever new public photos appear." });
  }
  return [...cards, REPAIR_BUDGET];
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
