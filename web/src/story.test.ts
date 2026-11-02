import { describe, expect, it } from "vitest";
import { PROBLEM } from "./benchmarks";
import { changeCards, compareLine, controlCheck, dotKey, hardestExample, photoSpan, recordFunnel, updateDots, visitLine } from "./story";
import type { AssetCheckSummary, AssetRow, Scan } from "./types";

const check = (over: Partial<AssetCheckSummary> = {}) => ({
  city: "Seattle", items: [], actions: { "Compare with the list": 26 }, no_visit: 43, compare_unsure: 7,
  totals: { in_view: 94, seen: 53, seen_by: { Survey: 27, "Second look": 9, "Second look, panorama": 17 }, not_determined: 41,
    not_listed: 33, not_listed_unsure: 3, seen_unsure: 4, newer: 52 },
  left_out: { repeats: 6, wrong: 1 },
  trust: { photo_months: ["2025-09", "2026-05", "2026-07"], looked_at: 31, sent_back: 5, outside_view: 2,
    controls: [{ photos: "Survey-date photos", asked: 17, both_yes: 0 }, { photos: "Panoramas", asked: 10, both_yes: 1 }] },
  ...over,
}) as AssetCheckSummary;
const survey = (over: Partial<Scan> = {}) => ({ id: 30, usd_per_km: 3.6, asset_check: check(), ...over }) as unknown as Scan;
const row = (over: Partial<AssetRow> = {}) => ({
  item: "street_tree", asset: "Street tree", action: "Visit on site", city_record: "TRE-1", lat: 47.6, lng: -122.3, ...over,
}) as AssetRow;

describe("the problem's facts", () => {
  it("are three, and each public one links its source", () => {
    expect(PROBLEM).toHaveLength(3);
    expect(PROBLEM.filter((f) => f.source)).toHaveLength(2);
  });
});

describe("from the city's records to the update list", () => {
  it("goes from the records in view to those that need no visit", () => {
    expect(recordFunnel(check()).map((s) => s.n)).toEqual([94, 27, 53, 43]);
  });
  it("counts a survey with no second look as found by the survey alone", () => {
    const totals = { ...check().totals, seen: 9, seen_by: {} };
    expect(recordFunnel(check({ totals })).map((s) => s.n)).toEqual([94, 0, 9, 43]);
  });
  it("says what the visits left are made of, in numbers that add up", () => {
    expect(visitLine(check())).toBe("The other 51 need a visit: 41 the photos could not settle and 10 were seen but not sure enough.");
    const totals = { ...check().totals, not_determined: 51 };
    expect(visitLine(check({ totals }))).toBe("The other 51 need a visit: 51 the photos could not settle.");
  });
  it("names the objects on no list and how many are not settled", () => {
    expect(compareLine(check())).toBe("26 more objects are not on the city's lists, 7 of them not settled.");
    expect(compareLine(check({ compare_unsure: 0 }))).toBe("26 more objects are not on the city's lists.");
    expect(compareLine(check({ actions: {} }))).toBeNull();
  });
});

describe("how far to trust it", () => {
  it("gives the first control's numbers and the others beside them", () => {
    expect(controlCheck(check())).toMatchObject({ big: "0 of 17", how: "1 of 10 in panoramas" });
    expect(controlCheck(check({ trust: { ...check().trust, controls: [] } }))).toBeNull();
  });
  it("picks the example with the smallest share that needs no visit", () => {
    const easy = { scan: survey({ asset_check: check({ no_visit: 63 }) }), name: "Capitol Hill" };
    const hard = { scan: survey({ asset_check: check({ no_visit: 9 }) }), name: "New York" };
    const none = { scan: survey({ asset_check: undefined }), name: "No list" };
    expect(hardestExample([easy, hard, none])?.name).toBe("New York");
    expect(hardestExample([none])).toBeNull();
  });
  it("says when the photos were taken", () => {
    expect(photoSpan(["2026-02"])).toBe("2026-02");
    expect(photoSpan(["2025-09", "2026-05", "2026-07"])).toBe("2025-09 to 2026-07");
    expect(photoSpan([])).toBeNull();
  });
});

describe("what changes", () => {
  it("says who gains what, from the survey's own numbers", () => {
    const cards = changeCards(survey(), "Seattle (Ballard)");
    expect(cards.map((c) => c.big)).toEqual(["51 of 94", "$3.60 /km", "3×"]);
    expect(cards[0].text).toContain("Seattle (Ballard)");
    expect(cards[0].text).toContain("Every row is a suggestion.");
    expect(cards[2].source).toContain("AMStatusCondRpt2020Final.pdf");
  });
  it("leaves out what the survey has no number for", () => {
    const cards = changeCards(survey({ asset_check: undefined, usd_per_km: null }), null);
    expect(cards.map((c) => c.big)).toEqual(["3×"]);
  });
});

describe("the update list on the map", () => {
  it("draws the city's records, marks the visits and leaves out objects on no list", () => {
    const dots = updateDots([row(), row({ action: "No change", city_record: "TRE-2" }),
      row({ action: "Compare with the list", city_record: "" })]);
    expect(dots.map((d) => [d.key, d.visit])).toEqual([["street_tree TRE-1", true], ["street_tree TRE-2", false]]);
    expect(dots[0].tip).toBe("Street tree TRE-1 · Visit on site");
  });
  it("keys a row the same way the map's click looks it up", () => {
    expect(updateDots([row()])[0].key).toBe(dotKey(row()));
  });
});
