import type { Scan } from "./types";

// The words of the results panel (docs/specs/2026-10-28-design-pass2-design.md 4), kept apart so they are tested.
export function cityLine(match: { found: number; covered: number } | undefined, hasCheck: boolean):
  { share: number | null; text: string } | null {
  if (!hasCheck) return null;
  if (!match) return { share: null, text: "No city list to compare" };
  if (match.covered === 0) return { share: null, text: "No city points in these photos" };
  return { share: match.found / match.covered, text: `${match.found} of ${match.covered} in the city's list` };
}

export function reviewChips(c: Scan["counts"]): { text: string; kind: "plain" | "site" }[] {
  return [
    { text: `${c.needs_review} waiting`, kind: "plain" },
    { text: `${c.field_check} to check on site`, kind: "site" },
    { text: `${c.verified} confirmed`, kind: "plain" },
    { text: `${c.rejected} rejected`, kind: "plain" },
    { text: c.dropped === null ? "set-aside not recorded" : `${c.dropped} set aside by the models`, kind: "plain" },
  ];
}
