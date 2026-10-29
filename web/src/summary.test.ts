import { describe, expect, it } from "vitest";
import { BENCHMARKS } from "./benchmarks";
import { checkLine, cityLine, clearBox, headline, itemNote, reviewProgress, thumbPosition, unsureCount } from "./summary";
import type { Scan } from "./types";

const counts = { defects: 2, obstructions: 0, entrances: 11, entrances_with_steps: 0, needs_review: 0,
  field_check: 4, verified: 2, rejected: 15, dropped: null };
const scan = { km: 0.94, usd_per_km: 4.11, cost_usd: 3.87, processing_seconds: 1152, review_seconds: 64 } as Scan;

describe("the results panel", () => {
  it("says how much of the city's list was found, as a share and words", () => {
    expect(cityLine({ found: 11, covered: 29 }, true)).toEqual({ share: 11 / 29, text: "11 of 29 in the city's list" });
    expect(cityLine({ found: 0, covered: 0 }, true)).toEqual({ share: null, text: "No city points in these photos" });
    expect(cityLine(undefined, true)).toEqual({ share: null, text: "No city list to compare" });
    expect(cityLine(undefined, false)).toBeNull(); // a survey without a city check shows no line
  });
  it("counts what the models were unsure of: waiting, confirmed, rejected or sent to a site check", () => {
    expect(unsureCount(counts)).toBe(21);
    expect(unsureCount({ ...counts, needs_review: 3 })).toBe(24);
  });
  it("leads with one number, the cost per km, and sets the walking survey's beside it", () => {
    expect(headline(scan, BENCHMARKS.US)).toEqual({
      big: "$4.11", unit: "per km", line: "0.94 km in 19.2 min + 1.1 min review · $3.87 in all",
      foot: "On foot: $106 per km, Seattle's 2017 sidewalk survey",
    });
    // no person's review yet: the processing time alone
    expect(headline({ ...scan, review_seconds: 0 }, BENCHMARKS.US).line).toBe("0.94 km in 19.2 min · $3.87 in all");
    // no published walking survey for the standard: no comparison
    expect(headline(scan, undefined).foot).toBeNull();
    // no street length: the whole cost is the number
    expect(headline({ ...scan, km: null, usd_per_km: null }, undefined))
      .toEqual({ big: "$3.87", unit: "in all", line: "19.2 min + 1.1 min review", foot: null });
  });
  it("says in one sentence what a person did with what the models were unsure of", () => {
    expect(checkLine({ ...counts, needs_review: 21, field_check: 0, verified: 0, rejected: 0 }))
      .toBe("21 the models were unsure of are waiting for a person.");
    expect(checkLine(counts))
      .toBe("A person checked the 21 the models were unsure of: 2 confirmed, 15 rejected, 4 to check on site.");
    expect(checkLine({ ...counts, needs_review: 16, field_check: 0, verified: 2, rejected: 3 }))
      .toBe("A person checked 5 of the 21 the models were unsure of; 16 are waiting.");
    expect(checkLine({ ...counts, field_check: 0, verified: 0, rejected: 0 })).toBe("The models were sure of every finding.");
  });
  it("puts an item's city share, defects and site checks on one line", () => {
    expect(itemNote({ city: "1 of 27 in the city's list", confirmed: 0, possible: 2, site: 0 }))
      .toBe("1 of 27 in the city's list · 2 possible defects");
    expect(itemNote({ city: null, confirmed: 1, possible: 0, site: 3 })).toBe("1 defect confirmed · 3 to check on site");
    expect(itemNote({ city: null, confirmed: 0, possible: 0, site: 0 })).toBe("");
  });
  it("counts a review's place: those looked at so far out of all the models were unsure of", () => {
    expect(reviewProgress({ ...counts, needs_review: 19, field_check: 0, verified: 1, rejected: 1 })).toEqual({ done: 2, total: 21 });
  });
  it("centres a thumbnail on the model's box (boxes are in thousandths of the photo)", () => {
    expect(thumbPosition([873, 0, 1000, 567])).toBe("93.7% 28.4%");
    expect(thumbPosition(null)).toBe("50% 50%");
    expect(thumbPosition([])).toBe("50% 50%");
  });
  it("calls a box clear when it is tall (40% of the photo or more), near the middle, and not a strip across or along the bottom", () => {
    expect(clearBox([420, 180, 520, 760])).toBe(true); // a pole
    expect(clearBox([40, 810, 960, 1000])).toBe(false); // a curb ramp across the photo's bottom
    expect(clearBox([300, 500, 900, 600])).toBe(false); // too short to see
    expect(clearBox([708, 139, 781, 405])).toBe(false); // a pole far away
    expect(clearBox([300, 400, 500, 995])).toBe(false); // cut off at the bottom edge
    expect(clearBox([830, 30, 880, 400])).toBe(false); // thin against the right edge
    expect(clearBox(null)).toBe(false);
  });
});
