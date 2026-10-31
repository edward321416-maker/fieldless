import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FindingList from "./FindingList";
import type { Finding } from "./items";
import ResultsPanel from "./ResultsPanel";
import type { Scan } from "./types";

const counts = { defects: 2, obstructions: 0, entrances: 0, entrances_with_steps: 0, needs_review: 21,
  field_check: 0, verified: 0, rejected: 0, dropped: null };
const scan = {
  id: 4, standard: "US", km: 0.94, usd_per_km: 4.11, cost_usd: 3.87, processing_seconds: 1152, review_seconds: 0,
  image_count: 181, captured_from: "2022-08-30", captured_to: "2022-08-30", status: "done", counts,
  items: [{ id: "curb_ramp", target: "curb ramp", map_icon: "ramp", kind: "check", inventory: false },
    { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }],
  city_check: { city: "Seattle", measured_at: "2026-10-28", method_url: "https://example.org/method",
    items: { street_tree: { found: 19, covered: 39, matched_ids: [] } } },
} as unknown as Scan;

const finding = (id: number, item: string, status = "auto", badge: Finding["badge"] = null): Finding => ({
  key: `issue:${id}`, item, lat: 47.61, lng: -122.32, heading: 0, box: [873, 0, 1000, 567], status, badge,
  title: item === "street_tree" ? "Street tree" : "Curb ramp without detectable warning",
  picked: { kind: "issue", item: { id, durable: false, captured_at: "2022-08-30T23:02:56Z",
    evidence: { url: `/api/evidence/scan4_issue${id}.jpg` } } } as unknown as Finding["picked"],
});
const findings = [finding(1, "curb_ramp", "needs_review", "defect"), finding(2, "curb_ramp", "needs_review", "defect"),
  ...Array.from({ length: 12 }, (_, i) => finding(10 + i, "street_tree", i === 0 ? "field_check" : "auto", i === 0 ? "site" : null))];

describe("the finished survey", () => {
  const html = renderToStaticMarkup(
    <ResultsPanel scan={scan} name="Capitol Hill" findings={findings} hidden={new Set(["curb_ramp"])}
      onToggle={() => undefined} onOpenItem={() => undefined} onReview={() => undefined} onIntro={() => undefined}
      hasReport />);
  it("leads with the cost per km, then one row per item with its city share and bar, then a collapsible review drawer and the files", () => {
    expect(html).toContain("$4.11");
    expect(html).toContain("per km");
    expect(html).toContain("0.94 km in 19.2 min · $3.87 in all");
    expect(html).toContain("On foot: $106 per km, Seattle&#x27;s 2017 sidewalk survey");
    expect(html).toContain("14 findings");
    expect(html).toContain("Curb ramp");
    expect(html).toContain("No city list to compare · 2 possible defects");
    expect(html).toContain("19 of 39 in the city&#x27;s list · 1 to check on site");
    expect(html).toContain("city-match-bar");
    expect(html).toContain("width:49%");
    expect(html).toContain("The models were unsure of 21: each is on the dispatch list to check on site.");
    expect(html).not.toContain(">Review<"); // read-only unless the panel is told otherwise
    expect(html).toContain("check-drawer");
    expect(html).toContain("Download for a GIS");
    expect(html).toContain('href="/api/scans/4/report" target="_blank"');
    expect(html).toContain(">Share</button>");
  });
  it("offers the report only when the server has it", () => {
    const without = renderToStaticMarkup(
      <ResultsPanel scan={scan} name={null} findings={findings} hidden={new Set()} onToggle={() => undefined}
        onOpenItem={() => undefined} onReview={() => undefined} onIntro={() => undefined} hasReport={false} />);
    expect(without).not.toContain("Survey report");
  });
  it("lets an item be hidden from its row, and drops the numbered steps and chips", () => {
    expect(html).toContain('aria-label="Show Curb ramp on the map"');
    expect(html).toContain('aria-label="Hide Street tree on the map"');
    expect(html).not.toContain("step-n");
    expect(html).not.toContain("Browse all");
  });
});

describe("the finished survey against the city's records", () => {
  it("keys the map's three shapes and says per item how its findings pair with the city's list", () => {
    const html = renderToStaticMarkup(
      <ResultsPanel scan={scan} name={null} findings={findings} hidden={new Set()} onToggle={() => undefined}
        onOpenItem={() => undefined} onReview={() => undefined} onIntro={() => undefined} hasReport={false}
        lens="records" cityCounts={{ street_tree: { matched: 13, notInList: 11, missed: 16, covered: 29 } }} />);
    expect(html).toContain("Found and in the city&#x27;s list");
    expect(html).toContain("Found, not in the city&#x27;s list");
    expect(html).toContain("In the city&#x27;s list, not found (to check)");
    expect(html).toContain("Paired by photo position and viewing direction");
    expect(html).toContain("13 matched · 11 not in the city&#x27;s list · 16 to check");
    expect(html).toContain('<span class="item-note">No city list to compare</span>');
    expect(html).not.toContain("19 of 39 in the city&#x27;s list");
  });
  it("keeps the findings' notes and no legend in the findings lens", () => {
    const html = renderToStaticMarkup(
      <ResultsPanel scan={scan} name={null} findings={findings} hidden={new Set()} onToggle={() => undefined}
        onOpenItem={() => undefined} onReview={() => undefined} onIntro={() => undefined} hasReport={false}
        lens="findings" cityCounts={{ street_tree: { matched: 13, notInList: 11, missed: 16, covered: 29 } }} />);
    expect(html).toContain("19 of 39 in the city&#x27;s list · 1 to check on site");
    expect(html).not.toContain("Paired by photo position");
  });
  it("gives an agent's item its own run's count against the city's list", () => {
    const withAgent = { ...scan, items: [...scan.items, { id: "agent_bike_rack", target: "bike rack", map_icon: "",
      kind: "check", inventory: true, added_by: "agent" }] } as unknown as Scan;
    const html = renderToStaticMarkup(
      <ResultsPanel scan={withAgent} name={null} findings={[...findings, finding(90, "agent_bike_rack")]}
        hidden={new Set()} onToggle={() => undefined} onOpenItem={() => undefined} onReview={() => undefined}
        onIntro={() => undefined} hasReport={false} agentCity={{ agent_bike_rack: { found: 14, covered: 22 } }}
        lens="records" cityCounts={{}} />);
    expect(html).toContain('<span class="item-note">14 of 22 in the city&#x27;s list</span>');
  });
});

describe("an item the agent added", () => {
  it("says so on its row and counts the city's list from its own run", () => {
    const withAgent = { ...scan, items: [...scan.items, { id: "agent_bike_rack", target: "bike rack", map_icon: "",
      kind: "check", inventory: true, added_by: "agent" }] } as unknown as Scan;
    const racks = [finding(90, "agent_bike_rack"), finding(91, "agent_bike_rack")];
    const html = renderToStaticMarkup(
      <ResultsPanel scan={withAgent} name={null} findings={[...findings, ...racks]} hidden={new Set()}
        onToggle={() => undefined} onOpenItem={() => undefined} onReview={() => undefined} onIntro={() => undefined}
        hasReport={false} agentCity={{ agent_bike_rack: { found: 14, covered: 22 } }}>
        <p>add-item panel</p>
      </ResultsPanel>);
    expect(html).toContain("Bike rack");
    expect(html).toContain("Added by the agent · not in the survey standard yet");
    expect(html).toContain("14 of 22 in the city&#x27;s list");
    expect(html.indexOf("add-item panel")).toBeLessThan(html.indexOf("Download for a GIS"));
  });
});

describe("one item's findings", () => {
  const trees = findings.filter((f) => f.item === "street_tree");
  const html = renderToStaticMarkup(
    <FindingList scan={scan} item="street_tree" findings={trees} streets={{ "issue:10": "E Pike St, near 11th Ave" }}
      selectedKey={null} onPick={() => undefined} onBack={() => undefined} />);
  it("lists each finding with its street, photo month, status and a thumbnail centred on the box", () => {
    expect(html).toContain("← All findings");
    expect(html).toContain("Street tree");
    expect(html).toContain("E Pike St, near 11th Ave");
    expect(html).toContain("Photo 2022-08");
    expect(html).toContain("Check on site");
    expect(html).toContain("object-position:93.7% 28.4%");
  });
  it("shows ten at first, then the rest on request, with the photos credited", () => {
    expect(html).toContain("Show 2 more");
    expect(html).toContain("Photos: Mapillary contributors, CC BY-SA 4.0");
  });
});
