import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ActionHub, { buildDispatchList, gisDiffGeoJson, workOrderCsv } from "./ActionHub";
import type { MissedRecord } from "./cityMatch";
import type { Finding } from "./items";
import type { Scan } from "./types";

const mockScan = {
  id: 17,
  standard: "US",
  km: 0.94,
  usd_per_km: 4.48,
  cost_usd: 4.21,
  processing_seconds: 420,
  review_seconds: 0,
  image_count: 181,
  captured_from: "2022-08-30",
  captured_to: "2022-08-30",
  status: "done",
  counts: { defects: 2, obstructions: 0, entrances: 1, entrances_with_steps: 1, needs_review: 1, field_check: 0, verified: 0, rejected: 0, dropped: null },
  items: [
    { id: "curb_ramp", target: "curb ramp", map_icon: "ramp", kind: "check", inventory: false },
    { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true },
  ],
  city_check: {
    city: "Seattle",
    measured_at: "2026-10-28",
    method_url: "https://example.org/method",
    items: {
      street_tree: {
        layer: "Trees",
        found: 10,
        covered: 15,
        matched_ids: ["T-1"],
        unmatched_records: [
          { item: "street_tree", id: "T-99", lat: 47.615, lng: -122.321, layer: "Trees", ids: ["T-99"] },
        ],
      },
    },
  },
} as unknown as Scan;

const makeFinding = (id: number, item: string, badge: Finding["badge"] = null, status = "auto"): Finding => ({
  key: `issue:${id}`,
  item,
  lat: 47.614,
  lng: -122.32,
  heading: 90,
  box: [100, 200, 300, 400],
  status,
  badge,
  title: badge === "defect" ? "Curb ramp without detectable warning" : "Street tree",
  picked: {
    kind: "issue",
    item: { id, durable: false, captured_at: "2022-08-30T23:02:56Z", evidence: { url: "/photo.jpg" } },
  } as unknown as Finding["picked"],
});

const makeEntrance = (id: number, stepCount: number): Finding => ({
  key: `entrance:${id}`,
  item: "entrance",
  lat: 47.6145,
  lng: -122.3205,
  heading: 90,
  box: null,
  status: "needs_review",
  badge: "review",
  title: "Shop Entrance",
  picked: {
    kind: "entrance",
    item: {
      id,
      name: "Retail Shop",
      step_count: stepCount,
      stairs_detected: stepCount > 0,
      sill_possible: false,
      label: { icon: "step", text: `${stepCount} steps at entrance` },
      review_status: "needs_review",
      review_reasons: [],
      evidence: { url: "/shop.jpg", creator: null, captured_at: null, link: "", license: "", blurred: false },
      captured_at: null,
      old_photo: false,
    },
  } as unknown as Finding["picked"],
});

describe("ActionHub work orders & GIS diff tools", () => {
  const findings: Finding[] = [
    makeFinding(1, "curb_ramp", "defect", "verified"),
    makeEntrance(2, 2), // the models were unsure: a site check, like any other item
    makeFinding(3, "street_tree", null, "needs_review"),
    makeFinding(4, "street_tree", null, "auto"), // nothing to do: no visit
  ];

  it("sends a crew to possible defects first, then to what the models were unsure of, and nowhere else", () => {
    const streets = { "issue:1": "East Pine Street", "entrance:2": "Broadway", "issue:3": "11th Ave" };
    const list = buildDispatchList(findings, mockScan, streets);
    expect(list.map((r) => r.workOrderId)).toEqual(["WO-US-17-P1-001", "WO-US-17-P2-001", "WO-US-17-P2-002"]);
    expect(list.map((r) => r.priority)).toEqual(["P1 · Repair", "P2 · Field verification", "P2 · Field verification"]);
    expect(list[0].street).toBe("East Pine Street");
    expect(list[1].recommendedAction).toBe("Check on site");
  });

  it("takes the items in turn, so the top of the list is not one item", () => {
    const many = [makeFinding(1, "a", null, "needs_review"), makeFinding(2, "a", null, "needs_review"),
      makeFinding(3, "a", null, "needs_review"), makeFinding(4, "b", null, "needs_review")];
    expect(buildDispatchList(many, mockScan).map((r) => r.finding.key))
      .toEqual(["issue:1", "issue:4", "issue:2", "issue:3"]);
  });

  it("states nothing the survey did not find: every row is one of its findings, with no other source", () => {
    for (const row of buildDispatchList(findings, mockScan)) {
      expect(Object.keys(row).sort()).toEqual(["assetType", "finding", "issueDescription", "oldPhoto", "photoDate",
        "priority", "recommendedAction", "street", "workOrderId"]);
    }
  });

  it("exports the work orders as a CSV, and text that starts like a formula stays text", () => {
    const list = buildDispatchList(findings, mockScan, { "issue:1": "East Pine Street" });
    const csv = workOrderCsv(list);
    expect(csv).toContain("work_order_id,priority,asset_type,issue_description,street_address,latitude,longitude,recommended_action,status,photo_evidence_url");
    expect(csv).toContain("P1 · Repair");
    expect(csv).toContain("East Pine Street");
    expect(csv).toContain("Inspect and repair");
    const formula = { ...list[0], issueDescription: "=HYPERLINK(1)" };
    expect(workOrderCsv([formula])).toContain(`"'=HYPERLINK(1)"`);
  });

  it("exports GIS diff as a valid GeoJSON FeatureCollection patch", () => {
    const unrecorded = [findings[0]];
    const missed: MissedRecord[] = [
      { key: "record:street_tree:0", item: "street_tree", lat: 47.615, lng: -122.321, layer: "Trees", fields: [["ID", "T-99"]] },
    ];
    const geojson = JSON.parse(gisDiffGeoJson(unrecorded, missed, mockScan));
    expect(geojson.type).toBe("FeatureCollection");
    expect(geojson.features).toHaveLength(2);
    expect(geojson.features[0].properties.diff_action).toBe("add_to_city_db");
    expect(geojson.features[1].properties.diff_action).toBe("verify_missing_city_record");
  });

  it("offers dispatch and the GIS diff, and lists the work orders", () => {
    const html = renderToStaticMarkup(
      <ActionHub
        scan={mockScan}
        findings={findings}
        streets={{ "issue:1": "Pine St" }}
        initialTab="dispatch"
      />,
    );
    expect(html).toContain(">Dispatch<");
    expect(html).toContain(">GIS Diff<");
    expect(html).toContain("send a crew only to");
    expect(html).toContain("Work order CSV");
  });
});
