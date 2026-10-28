import { describe, expect, it } from "vitest";
import { cityLine, reviewChips, unsureCount } from "./summary";

const counts = { defects: 2, obstructions: 0, entrances: 11, entrances_with_steps: 0, needs_review: 0,
  field_check: 4, verified: 2, rejected: 15, dropped: null };

describe("the results panel", () => {
  it("says how much of the city's list was found, as a bar share and words", () => {
    expect(cityLine({ found: 11, covered: 29 }, true)).toEqual({ share: 11 / 29, text: "11 of 29 in the city's list" });
    expect(cityLine({ found: 0, covered: 0 }, true)).toEqual({ share: null, text: "No city points in these photos" });
    expect(cityLine(undefined, true)).toEqual({ share: null, text: "No city list to compare" });
    expect(cityLine(undefined, false)).toBeNull(); // a survey without a city check shows no line
  });
  it("puts a person's review in one row of chips, set-aside included when recorded", () => {
    expect(reviewChips(counts)).toEqual([
      { text: "4 to check on site", kind: "site" },
      { text: "2 confirmed", kind: "plain" }, { text: "15 rejected", kind: "plain" },
      { text: "set-aside not recorded", kind: "plain" }]);
    expect(reviewChips({ ...counts, dropped: 7 })[3]).toEqual({ text: "7 set aside by the models", kind: "plain" });
    expect(reviewChips({ ...counts, needs_review: 2 })[0]).toEqual({ text: "2 waiting", kind: "plain" });
  });
  it("counts what the models were unsure of: waiting, confirmed, rejected or sent to a site check", () => {
    expect(unsureCount(counts)).toBe(21);
    expect(unsureCount({ ...counts, needs_review: 3 })).toBe(24);
  });
});
