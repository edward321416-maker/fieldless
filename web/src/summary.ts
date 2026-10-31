import type { Benchmark } from "./benchmarks";
import { dollars } from "./format";
import { capital } from "./items";
import type { Scan } from "./types";

// The words of the results panel (docs/specs/2026-10-29-cleanup-design.md 4), kept apart so they are tested.
export function cityLine(match: { found: number; covered: number } | undefined, hasCheck: boolean):
  { share: number | null; text: string } | null {
  if (!hasCheck) return null;
  if (!match) return { share: null, text: "No city list to compare" };
  if (match.covered === 0) return { share: null, text: "No city points in these photos" };
  return { share: match.found / match.covered, text: `${match.found} of ${match.covered} in the city's list` };
}

// The findings the models were unsure of, whether someone has looked at them yet or not.
export const unsureCount = (c: Scan["counts"]) => c.needs_review + c.verified + c.rejected + c.field_check;

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const minutes = (seconds: number) => (seconds / 60).toFixed(1);

// What a survey's status means for the person looking at it; the raw error stays available under Details.
export function statusNote(scan: Pick<Scan, "status" | "error">): string | null {
  if (scan.status === "queued") return "Waiting for the survey ahead of it to finish.";
  if (scan.status === "stopped_cost_cap")
    return "Stopped at the spending cap ($3 per survey, $10 per day across all surveys). Results so far are shown.";
  if (scan.status === "failed") {
    if (scan.error?.startsWith("NoPhotos")) return "No recent pedestrian photos were found here. Try a nearby street.";
    if (scan.error && /start (a new survey|it again)/.test(scan.error)) return capital(scan.error); // written for this screen
    return "The survey did not finish every step. Any results it produced are shown.";
  }
  return null;
}

// One number on top, the cost per km (after Treepedia's single figure), the time and whole cost under it, and the
// published walking survey beside it when the standard has one (docs/ui-rules.md U1, U7). A person's review time
// is added only when someone reviewed.
export function headline(scan: Pick<Scan, "km" | "usd_per_km" | "cost_usd" | "processing_seconds" | "review_seconds">,
  bench: Benchmark | undefined): { big: string; unit: string; line: string; foot: string | null } {
  const time = `${minutes(scan.processing_seconds)} min${scan.review_seconds > 0 ? ` + ${minutes(scan.review_seconds)} min review` : ""}`;
  const perKm = scan.usd_per_km !== null && scan.km !== null;
  return {
    big: dollars(perKm ? scan.usd_per_km! : scan.cost_usd),
    unit: perKm ? "per km" : "in all",
    line: perKm ? `${scan.km!.toFixed(2)} km in ${time} · ${dollars(scan.cost_usd)} in all` : time,
    foot: bench && perKm ? `On foot: $${bench.usd_per_km} per km, ${bench.name}` : null,
  };
}

// What a person did with what the models were unsure of, in one sentence (the chips it replaces said the same).
export function checkLine(c: Scan["counts"]): string {
  const unsure = unsureCount(c);
  const looked = unsure - c.needs_review;
  if (unsure === 0) return "The models were sure of every finding.";
  if (looked === 0) return `The models were unsure of ${unsure}: each is on the dispatch list to check on site.`;
  if (c.needs_review > 0) return `A person checked ${looked} of the ${unsure} the models were unsure of; ${c.needs_review} are waiting.`;
  return `A person checked the ${unsure} the models were unsure of: ${c.verified} confirmed, ${c.rejected} rejected, ` +
    `${c.field_check} to check on site.`;
}

// The line under an item's name: the city's list, then what a person may have to act on.
export function itemNote(n: { city: string | null; confirmed: number; possible: number; site: number }): string {
  return [
    n.city,
    n.confirmed > 0 ? `${plural(n.confirmed, "defect")} confirmed` : null,
    n.possible > 0 ? plural(n.possible, "possible defect") : null,
    n.site > 0 ? `${n.site} to check on site` : null,
  ].filter(Boolean).join(" · ");
}

// Where a review stands: how many of the findings the models were unsure of a person has looked at.
export function reviewProgress(c: Scan["counts"]): { done: number; total: number } {
  const total = unsureCount(c);
  return { done: total - c.needs_review, total };
}

// A box that reads at a glance in a small photo: at least 40% of its height, near the middle, not a strip across the
// photo or along its bottom edge (the introduction's photo, 10/29 judge walk: a curb ramp's box spanned the bottom of
// the photo, and the next picks, poles, sat small or thin against the right edge).
export function clearBox(box: number[] | null): boolean {
  if (!box || box.length < 4) return false;
  const [x1, y1, x2, y2] = box;
  const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
  return y2 - y1 >= 400 && x2 - x1 <= 600 && y2 < 990 && cx >= 200 && cx <= 800 && cy >= 200 && cy <= 800;
}

// A thumbnail's object-position, centred on the model's box (boxes are in thousandths of the photo).
export function thumbPosition(box: number[] | null): string {
  if (!box || box.length < 4) return "50% 50%";
  return `${Math.round((box[0] + box[2]) / 2) / 10}% ${Math.round((box[1] + box[3]) / 2) / 10}%`;
}
