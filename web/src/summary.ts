import type { Scan } from "./types";

// The words of the results panel (docs/specs/2026-10-28-design-pass2-design.md 4), kept apart so they are tested.
export function cityLine(match: { found: number; covered: number } | undefined, hasCheck: boolean):
  { share: number | null; text: string } | null {
  if (!hasCheck) return null;
  if (!match) return { share: null, text: "No city list to compare" };
  if (match.covered === 0) return { share: null, text: "No city points in these photos" };
  return { share: match.found / match.covered, text: `${match.found} of ${match.covered} in the city's list` };
}

// The findings a person is asked about: those the models were unsure of, whether looked at yet or not.
export const unsureCount = (c: Scan["counts"]) => c.needs_review + c.verified + c.rejected + c.field_check;

export function reviewChips(c: Scan["counts"]): { text: string; kind: "plain" | "site" }[] {
  return [
    ...(c.needs_review > 0 ? [{ text: `${c.needs_review} waiting`, kind: "plain" as const }] : []),
    { text: `${c.field_check} to check on site`, kind: "site" },
    { text: `${c.verified} confirmed`, kind: "plain" },
    { text: `${c.rejected} rejected`, kind: "plain" },
    { text: c.dropped === null ? "set-aside not recorded" : `${c.dropped} set aside by the models`, kind: "plain" },
  ];
}
