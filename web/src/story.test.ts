import { describe, expect, it } from "vitest";
import { PROBLEM } from "./benchmarks";
import { changeCards, compareLine, controlCheck, dotKey, hardestExample, nearbyOf, photoSpan, recordFunnel, updateDots, visitLine } from "./story";
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
  it("each links its source, but for the builder's own job", () => {
    expect(PROBLEM.filter((f) => !f.source).map((f) => f.title)).toEqual(["One district's street lights"]);
  });
  it("say what the job was like in the builder's words", () => {
    const own = PROBLEM.find((f) => !f.source);
    expect(own?.text).toContain("about twenty of us");
    expect(own?.text).toContain("for three months");
    expect(own?.text).toContain("wasn't marked exactly, so we kept looking around to find it");
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
  const full = check({ visits_with_photo: 51, date_gap_years: 8.2, actions: { "Update the date": 26 } });
  const run = { target: "fire hydrant", cost_usd: 0.613, survey: "Seattle (Capitol Hill)" };
  it("says who gains what, from the survey's own numbers", () => {
    const cards = changeCards(full, "Seattle (Ballard)", run);
    expect(cards.map((c) => c.big)).toEqual(["51 of 94", "8.2 years", "$0.61"]);
    expect(cards[0].text).toBe("places still need a visit in Seattle (Ballard), each with a survey photo from nearby. See what the place looks like before you leave. Every row is a suggestion.");
    expect(cards[1].text).toBe("median gap between the city's date and a newer photo, on 26 records in Seattle (Ballard). Review each from a desk; the city decides.");
    expect(cards[2].text).toBe('Muse Spark added "fire hydrant" to a finished survey of Seattle (Capitol Hill). Nobody walked the street again.');
  });
  it("promises a photo only when every visit has one", () => {
    const [first] = changeCards(check({ visits_with_photo: 50 }), "Ballard");
    expect(first.text).toBe("places still need a visit in Ballard. Every row is a suggestion.");
  });
  it("leaves out what the survey has no number for", () => {
    expect(changeCards(undefined, null)).toEqual([]);
    expect(changeCards(check(), "Ballard").map((c) => c.big)).toEqual(["51 of 94"]);
  });
});

describe("the photo near a city record", () => {
  const nearby = { link: "l", creator: "a", captured_at: null, license: "CC BY-SA 4.0", dist_m: 14 };
  const record = { key: "k", item: "street_tree", layer: "l", lat: 0, lng: 0, fields: [["UNITID", "TRE-1"]] } as never;
  it("comes from the row of the same item and record", () => {
    expect(nearbyOf([row({ city_record: "TRE-1", nearby_photo: nearby })], record)).toBe(nearby);
    expect(nearbyOf([row({ city_record: "TRE-2", nearby_photo: nearby })], record)).toBeUndefined();
    expect(nearbyOf([row({ city_record: "TRE-1", item: "pole", nearby_photo: nearby })], record)).toBeUndefined();
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
