import { describe, expect, it } from "vitest";
import { cityCounts, cityStates, cityTotals, missedRecords } from "./cityMatch";
import type { CityCheck, Issue } from "./types";

const city = {
  scan_id: 17, city: "Seattle", measured_at: "2026-10-29", reviewed: false, method: "", method_url: "",
  items: {
    street_tree: { layer: "SDOT street trees", found: 2, covered: 5, matched_ids: [1, 2, 3], rejected_at_city_point: [],
      id_fields: ["COMPKEY", "UNITID"],
      missed: [{ lat: 47.61, lng: -122.32, city: { COMPKEY: 222410, UNITID: "TRE-54698" } },
        { lat: 47.62, lng: -122.31, city: { COMPKEY: 222411, UNITID: "TRE-54699" } }] },
    litter_can: { layer: "SPU litter cans", found: 0, covered: 1, matched_ids: [], rejected_at_city_point: [] },
  },
} as unknown as CityCheck;
const issue = (id: number | string, type: string, review = "auto") =>
  ({ id, issue_type: type, review_status: review }) as unknown as Issue;
const issues = [issue(1, "street_tree"), issue(2, "street_tree"), issue(3, "street_tree"), issue(4, "street_tree"),
  issue(5, "street_tree", "rejected"), issue(6, "litter_can"), issue(7, "curb_ramp_warning"), issue("item5-1", "agent_fire_hydrant")];

describe("the survey against the city's own records", () => {
  it("counts per item: city records found, findings with no record nearby, records in view not found", () => {
    expect(cityCounts(city, issues)).toEqual({
      street_tree: { matched: 2, notInList: 1, missed: 3, covered: 5 },
      litter_can: { matched: 0, notInList: 1, missed: 1, covered: 1 },
    });
    expect(cityCounts(undefined, issues)).toEqual({});
  });
  it("adds the items up, and says which field names the city keeps a record by", () => {
    expect(cityTotals(city, issues)).toEqual({ matched: 2, notInList: 2, missed: 4, covered: 6, fields: ["COMPKEY", "UNITID"] });
    expect(cityTotals(undefined, issues)).toBeNull();
  });
  it("gives each finding of an item with a list its state; a rejected one or an item without a list gets none", () => {
    expect(cityStates(city, issues)).toEqual({
      "issue:1": "in_list", "issue:2": "in_list", "issue:3": "in_list", "issue:4": "not_in_list", "issue:6": "not_in_list",
    });
  });
  it("lists the records in view the survey did not find, with the city's own ids", () => {
    expect(missedRecords(city)).toEqual([
      { key: "record:street_tree:0", item: "street_tree", layer: "SDOT street trees", lat: 47.61, lng: -122.32,
        fields: [["COMPKEY", "222410"], ["UNITID", "TRE-54698"]] },
      { key: "record:street_tree:1", item: "street_tree", layer: "SDOT street trees", lat: 47.62, lng: -122.31,
        fields: [["COMPKEY", "222411"], ["UNITID", "TRE-54699"]] },
    ]);
  });
});
