import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActionHub, { buildDispatchList, gisDiffGeoJson, workOrderCsv } from "./ActionHub";
import type { MissedRecord } from "./cityMatch";
import type { Finding } from "./items";
import { MiniCredit } from "./Photo";
import type { Evidence, Scan } from "./types";

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

const evidence = (key: string, creator: string | null = "pat"): Evidence => ({
  url: `/api/evidence/${key}.jpg`, creator, captured_at: "2022-08-30T23:02:56Z",
  link: `https://www.mapillary.com/app/?pKey=${key}`, license: "CC BY-SA 4.0", blurred: true,
});

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
    item: { id, issue_type: item, durable: false, captured_at: "2022-08-30T23:02:56Z", evidence: evidence(`p${id}`) },
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
      evidence: evidence("shop", null),
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
    expect(list.map((r) => r.priority)).toEqual(["P1 · Possible defect", "P2 · Field verification", "P2 · Field verification"]);
    expect(list[0].street).toBe("East Pine Street");
    expect(list[0].recommendedAction).toBe("Check on site");
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
    expect(csv).toContain("work_order_id,priority,asset_type,issue_description,street_address,latitude,longitude,recommended_action,status,photo_evidence_url,photo_creator,photo_link,photo_license");
    expect(csv).toContain("P1 · Possible defect");
    expect(csv).toContain("East Pine Street");
    expect(csv).not.toContain("Inspect and repair");
    expect(csv).toContain("Check on site");
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
    expect(geojson.features[0].properties.diff_action).toBe("check_not_on_city_list");
    expect(geojson.features[1].properties.diff_action).toBe("check_city_record_not_found");
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
    expect(html).toContain("findings to check on site");
    expect(html).not.toContain("send a crew");
    expect(html).toContain("Work order CSV");
  });

  it("leads the dispatch list with what to check on site, not with a repair", () => {
    const html = renderToStaticMarkup(
      <ActionHub scan={mockScan} findings={findings} initialTab="dispatch" />,
    );
    expect(html).toContain("findings to check on site: possible defects first, then what the models were unsure of, each with its photo.");
    expect(html).toContain("P1 Possible defect");
    expect(html).not.toContain("locations");
    expect(html).not.toContain("Inspect and repair");
    expect(html).not.toContain("P1 Repair");
    const more = [...findings, makeFinding(5, "street_tree", null, "needs_review"), makeFinding(6, "street_tree", null, "needs_review")];
    const long = renderToStaticMarkup(<ActionHub scan={mockScan} findings={more} initialTab="dispatch" />);
    expect(long).toContain(">Show all 5<");
  });

  it("never tells the city to change its list", () => {
    const html = renderToStaticMarkup(
      <ActionHub scan={mockScan} findings={findings} initialTab="gis" />,
    );
    // "patch" as a word: the tab is called Dispatch, which holds the letters
    for (const word of ["Add to DB", "Unrecorded", "confirmed", "GPS"]) expect(html).not.toContain(word);
    expect(html).not.toMatch(/\bpatch\b/i);
    expect(html).toContain("Against Seattle&#x27;s records:");
    expect(html).toContain("found but not on the list");
    expect(html).toContain("in the photos&#x27; view but not found. The city decides what to do with each.");
    expect(html).toContain("Not on the list · To check");
    expect(html).toContain("Not found · To check");
    expect(html).toContain("Found in a photo · Not on the city&#x27;s list");
    expect(html).toContain("Check against the city&#x27;s list");
    expect(html).toContain("Near 47.61400, -122.32000");
    expect(html).toContain("Differences (.geojson)");
    expect(html).not.toMatch(/>[+-]\d/); // no signed counts: the city decides what a difference means
  });
});

describe("every photo carries its creator, the Mapillary logo and a link", () => {
  const findings: Finding[] = [
    makeFinding(1, "curb_ramp", "defect", "verified"),
    makeEntrance(2, 2),
    makeFinding(3, "street_tree", null, "needs_review"),
  ];
  afterEach(() => vi.unstubAllGlobals());

  it("exports each photo's creator, link and licence with a full address", () => {
    vi.stubGlobal("window", { location: { origin: "https://fieldless.example" } });
    const csv = workOrderCsv(buildDispatchList(findings, mockScan));
    const [header, first, ...rest] = csv.split("\n");
    expect(header).toContain("photo_evidence_url,photo_creator,photo_link,photo_license");
    expect(csv).not.toMatch(/"\/api\/evidence/);
    expect(first.endsWith(
      `"https://fieldless.example/api/evidence/p1.jpg","pat","https://www.mapillary.com/app/?pKey=p1","CC BY-SA 4.0"`)).toBe(true);
    expect(rest.some((line) => line.endsWith(`"https://fieldless.example/api/evidence/shop.jpg","","https://www.mapillary.com/app/?pKey=shop","CC BY-SA 4.0"`))).toBe(true);
  });

  it("leaves the photo's address as it is where there is no window to name the site", () => {
    expect(typeof window).toBe("undefined");
    const csv = workOrderCsv(buildDispatchList(findings, mockScan));
    expect(csv).toContain('"/api/evidence/p1.jpg"');
  });

  it("leaves the columns empty for a finding with no photo", () => {
    const bare = { ...findings[0], picked: { ...findings[0].picked, item: { ...findings[0].picked.item, evidence: null } } } as Finding;
    const csv = workOrderCsv(buildDispatchList([bare], mockScan));
    expect(csv.split("\n")[1].endsWith('"","","",""')).toBe(true);
  });

  it("puts the creator, link and licence on each difference's GeoJSON point, with a full address", () => {
    vi.stubGlobal("window", { location: { origin: "https://fieldless.example" } });
    const [point] = JSON.parse(gisDiffGeoJson([findings[0], findings[1]], [], mockScan)).features;
    expect(point.properties).toMatchObject({
      evidence_photo: "https://fieldless.example/api/evidence/p1.jpg", photo_creator: "pat",
      photo_link: "https://www.mapillary.com/app/?pKey=p1", photo_license: "CC BY-SA 4.0",
    });
    const second = JSON.parse(gisDiffGeoJson([findings[1]], [], mockScan)).features[0];
    expect(second.properties.photo_creator).toBeNull(); // a photo with no named creator
  });

  it("credits the photo on every dispatch card and every GIS diff card", () => {
    for (const tab of ["dispatch", "gis"] as const) {
      const html = renderToStaticMarkup(<ActionHub scan={mockScan} findings={findings} initialTab={tab} />);
      const cards = html.match(/class="action-card"/g)?.length ?? 0;
      expect(cards).toBeGreaterThan(0);
      expect(html.match(/class="credit credit-mini"/g)?.length).toBe(cards);
      expect(html).toContain("CC BY-SA 4.0</a>");
      expect(html).toContain('class="mapillary-logo"');
    }
    const html = renderToStaticMarkup(<ActionHub scan={mockScan} findings={findings} initialTab="dispatch" />);
    expect(html).toContain('href="https://www.mapillary.com/app/?pKey=p1"');
    expect(html).toContain("Mapillary contributor ·"); // the entrance photo has no named creator
  });

  it("opens a credit's link by itself: its click, Enter and Space do not reach the card around it", () => {
    const credit = MiniCredit({ evidence: evidence("p1") }) as ReactElement<{
      onClick: (e: { stopPropagation: () => void }) => void; onKeyDown: (e: { key: string; stopPropagation: () => void }) => void;
    }>;
    const click = { stopPropagation: vi.fn() };
    credit.props.onClick(click);
    expect(click.stopPropagation).toHaveBeenCalledTimes(1);
    for (const key of ["Enter", " "]) {
      const press = { key, stopPropagation: vi.fn() };
      credit.props.onKeyDown(press);
      expect(press.stopPropagation).toHaveBeenCalledTimes(1);
    }
    for (const key of ["Escape", "ArrowDown", "Tab"]) { // the rest reach the window's own handlers
      const press = { key, stopPropagation: vi.fn() };
      credit.props.onKeyDown(press);
      expect(press.stopPropagation).not.toHaveBeenCalled();
    }
  });
});

describe("a city list shown as counts only", () => {
  const seoulScan = { ...mockScan, standard: "KR", city_check: {
    scan_id: 17, city: "Seoul", measured_at: "2026-10-29", reviewed: false, method: "", method_url: "", counts_only: true,
    source: "Seoul Open Data Plaza OA-23040, KOGL Type 4",
    items: { street_tree: { layer: "Seoul street trees", found: 6, covered: 23, matched_ids: [], rejected_at_city_point: [],
      id_fields: [], pairs: [], missed: [] } } } } as unknown as Scan;
  const some = [makeFinding(3, "street_tree", null, "needs_review"), makeFinding(4, "street_tree", null, "auto")];

  it("has no GIS Diff: there are no records to set against the survey", () => {
    const html = renderToStaticMarkup(<ActionHub scan={seoulScan} findings={some} initialTab="dispatch" />);
    expect(html).toContain(">Dispatch<");
    expect(html).not.toContain("GIS Diff");
  });

  it("opens on Dispatch when asked for the GIS Diff, and calls no finding unlisted", () => {
    const html = renderToStaticMarkup(<ActionHub scan={seoulScan} findings={some} initialTab="gis" />);
    expect(html).toContain("findings to check on site");
    expect(html).toContain("Work order CSV");
    for (const text of ["Against Seoul", "not on the list", "Not on the list", "not on the city", "Differences (.geojson)"]) {
      expect(html).not.toContain(text);
    }
    expect(html).not.toContain("city&#x27;s list");
  });
});

describe("a survey with an update list", () => {
  const counts = { in_view: 94, seen: 53, seen_by: {}, not_determined: 41, not_listed: 33, not_listed_unsure: 3, seen_unsure: 4, newer: 52 };
  const actions = { "Update the date": 27, "Visit on site": 58 };
  const withList = { ...mockScan, asset_check: { city: "Seattle", items: [], totals: counts, actions, no_visit: 43,
    left_out: { repeats: 6, wrong: 1 }, trust: { photo_months: [], controls: [], looked_at: 0, sent_back: 0, outside_view: 0 } } } as typeof mockScan;

  it("shows the list and neither of the tabs it stands in for", () => {
    for (const tab of [undefined, "dispatch", "gis"] as const) {
      const html = renderToStaticMarkup(<ActionHub scan={withList} findings={[]} initialTab={tab} />);
      expect(html).toContain("Update list");
      expect(html).toContain("43/94");
      expect(html).not.toContain("Dispatch");
      expect(html).not.toContain("GIS Diff");
    }
  });

  it("leaves a survey without one as it was", () => {
    const html = renderToStaticMarkup(<ActionHub scan={mockScan} findings={[]} initialTab="dispatch" />);
    expect(html).toContain("Dispatch");
    expect(html).not.toContain("Update list");
  });
});
