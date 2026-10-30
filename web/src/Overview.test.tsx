import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Overview from "./Overview";
import type { Issue, ItemRun, Scan } from "./types";

const scan = {
  id: 17, standard: "US", km: 0.94, usd_per_km: 4.48, cost_usd: 4.21, processing_seconds: 420, review_seconds: 0,
  image_count: 181, status: "done",
  items: [{ id: "utility_pole", target: "utility or streetlight pole", map_icon: "street-light", kind: "check", inventory: true },
    { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }],
  checks: Array.from({ length: 5 }, (_, i) => ({ id: `check${i}` })),
  city_check: { scan_id: 17, city: "Seattle", measured_at: "2026-10-28", reviewed: false, method: "",
    method_url: "https://example.org/method",
    items: { street_tree: { layer: "SDOT street trees", found: 2, covered: 5, matched_ids: [1], rejected_at_city_point: [],
      id_fields: ["COMPKEY", "UNITID"] } } },
} as unknown as Scan;
const opinion = { answer: "unclear", agree: false, model: "Muse Spark Open" };
const issue = (id: number, item: string, over: Partial<Issue> = {}) =>
  ({ id, issue_type: item, review_status: "auto", second_opinion: null, ...over }) as unknown as Issue;
// five kept rows of the listed item (one paired with a city record), one rejected; two answered by a second model
const issues = [issue(1, "street_tree"), issue(2, "street_tree"), issue(3, "street_tree", { second_opinion: opinion } as Partial<Issue>),
  issue(4, "street_tree"), issue(5, "street_tree"), issue(6, "street_tree", { review_status: "rejected" }),
  issue(7, "utility_pole", { second_opinion: opinion } as Partial<Issue>)];
const examples = [{ item: "utility_pole", url: "/api/evidence/p.jpg", box: [500, 100, 520, 600] },
  { item: "street_tree", url: "/api/evidence/t.jpg", box: null }];
const run = {
  id: 5, scan_id: 17, target: "fire hydrant", status: "done", photos: [], item: null, cost_usd: 0.613, error: null,
  created_at: "2026-10-30T00:25:44Z",
  progress: [
    "Muse Spark drafted the item: find \"fireplug\", ask \"A fire hydrant is ...\"",
    "[1] SAM 3.1 finds \"fireplug\": 3 of 4 photos that show it, 0 of 14 that do not",
    "[2] SAM 3.1 finds \"fire hydrant\": 4 of 4 photos that show it, 0 of 14 that do not",
    "[3] SAM 3.1 finds \"red hydrant\": 0 of 4 photos that show it, 0 of 14 that do not",
    "[4] SAM 3.1 finds \"yellow hydrant\": 3 of 4 photos that show it, 0 of 14 that do not",
  ],
  issues: Array.from({ length: 5 }, (_, i) => issue(100 + i, "agent_fire_hydrant")),
  test: { labels: "model", judge: "Muse Spark Open", layer: null, phrase: "fire hydrant", ask: "", calls: 5, found: [4, 4],
    kept: [4, 4], false: [0, 14] },
} as unknown as ItemRun;
const seongsu = {
  id: 1, standard: "KR", km: 0.58, usd_per_km: 2.52, cost_usd: 1.46, processing_seconds: 397, review_seconds: 0,
  image_count: 103, status: "done", items: [], checks: [{ id: "check0" }],
  city_check: { scan_id: 1, city: "Seoul", measured_at: "2026-10-28", reviewed: false, method: "", method_url: "",
    items: { street_tree: { layer: "Seoul street trees", found: 6, covered: 23, matched_ids: [], rejected_at_city_point: [] } } },
} as unknown as Scan;

const render = (over: Partial<Parameters<typeof Overview>[0]> = {}) => renderToStaticMarkup(
  <Overview scan={scan} name="Capitol Hill" issues={issues} counts={{ utility_pole: 1, street_tree: 5 }}
    examples={examples} runs={[run]} others={[{ scan: seongsu, name: "Seongsu" }]} canSurvey onSee={() => undefined}
    onSurvey={() => undefined} onOpenScan={() => undefined} onOpenRun={() => undefined} {...over} />);
const count = (html: string, s: string) => html.split(s).length - 1;

describe("the overview", () => {
  const html = render();
  it("opens with what Fieldless does and its cost per km beside walking, then where to go", () => {
    expect(html).toContain("Street surveys without the fieldwork");
    expect(html).toContain("$106");
    expect(html).toContain("$4.48");
    expect(html).toContain("0.94 km in 7.0 min · $4.21 in all");
    expect(html).toContain("Seattle&#x27;s 2017 sidewalk survey: $400,000 for 2,336 miles.");
    expect(html).toContain("See the finished survey of Capitol Hill");
    expect(html).toContain("Survey your own streets");
    expect(html.indexOf("$4.48")).toBeLessThan(html.indexOf("One pass, every item"));
  });
  it("shows one card of the same kind per item with findings, with its photo, count and city line", () => {
    expect(html).toContain("One pass, every item");
    expect(count(html, 'class="overview-card overview-item"')).toBe(2);
    expect(html).toContain('alt="Utility or streetlight pole in a street photo"');
    expect(html).toContain('alt="Street tree in a street photo"');
    expect(html).toContain('loading="lazy"');
    expect(html).toContain("object-position:51% 35%");
    expect(html).toContain("1 finding<");
    expect(html).toContain("5 findings");
    expect(html).toContain("2 of 5 in the city&#x27;s list");
    expect(html).toContain("No city list to compare");
    expect(html).toContain("Photos: Mapillary contributors, CC BY-SA 4.0");
    const fewer = render({ counts: { utility_pole: 0, street_tree: 5 } });
    expect(count(fewer, 'class="overview-card overview-item"')).toBe(1);
    expect(fewer).not.toContain("Utility or streetlight pole in a street photo");
  });
  it("draws how the rules, the city's records and the photos become three outcomes and go back to the city", () => {
    expect(html).toContain("How the pieces fit");
    expect(html).toContain("5 questions in the US standard");
    expect(html).toContain("1 city list");
    expect(html).toContain("SDOT street trees");
    expect(html).toContain("181 public Mapillary photos");
    expect(html).toContain("SAM 3.1 finds each item · Muse Spark judges it and says why · Muse Spark Open double-checks possible defects");
    expect(html).toMatch(/>2<\/strong><span[^>]*>Found and in the city&#x27;s list</);
    expect(html).toMatch(/>4<\/strong><span[^>]*>Found, not in the city&#x27;s list</);
    expect(html).toMatch(/>3<\/strong><span[^>]*>In the city&#x27;s list, not found</);
    expect(html).toContain("a list to check");
    expect(html).toContain("Back to the city: GeoJSON and a CSV per item in the city&#x27;s own field names (COMPKEY, UNITID), and a survey report.");
  });
  it("backs the results with the city's records, a second model, a check by eye and another city", () => {
    expect(html).toContain("Checked, not assumed");
    expect(html).toContain("2 of 5");
    expect(html).toContain("of the city&#x27;s recorded assets in the photos&#x27; view were found");
    expect(html).toMatch(/>2<\/strong><p>possible defects asked again by Muse Spark Open, an open-weight model; when the two disagree the finding stays unclear</);
    expect(html).toContain("73 of 86");
    expect(html).toContain("findings the models kept were right when each photo was checked");
    expect(html).toContain("Judged on each evidence photo by the AI coding assistant, not by a person.");
    expect(html).toContain("6 of 23");
    expect(html).toContain("Seongsu: Seoul street trees");
  });
  it("shows the item the agent wrote: the phrases it tried, the one it chose, what it kept, found and cost", () => {
    expect(html).toContain("New items, written by Muse Spark");
    expect(html).toContain("Fire hydrant");
    expect(html).toContain("Muse Spark Open saw it in 4 of 18 photos");
    expect(html).toContain("<li>fireplug 3/4</li>");
    expect(html).toContain("<strong>fire hydrant 4/4</strong> chosen");
    expect(html).toContain("<li>red hydrant 0/4</li>");
    expect(html.indexOf("fireplug 3/4")).toBeLessThan(html.indexOf("yellow hydrant 3/4"));
    expect(html).toContain("Kept 4 of 4 that show it, 0 of 14 without it");
    expect(html).toContain("5 found in this survey · $0.61");
    expect(html).toContain("See it on the map");
  });
  it("offers each example survey with its rulebook, cost per km, photos and first city line", () => {
    expect(html).toContain("Two cities, two rulebooks");
    expect(html).toContain("US standard");
    expect(html).toContain("Korean standard");
    expect(html).toContain("$2.52 per km");
    expect(html).toContain("103 photos");
    expect(html).toContain("2 of 5 in SDOT street trees");
    expect(html).toContain("6 of 23 in Seoul street trees");
    expect(html).toContain("Built with Mapillary, SAM 3.1, Muse Spark, Muse Spark Open and Muse Glimmer");
  });
  it("gives the page a heading outline and keyboard buttons", () => {
    expect(html).toContain('<section class="overview" aria-labelledby="overview-title">');
    expect(count(html, "<h3 id=")).toBe(5);
    expect(html).not.toContain("<button>");
    expect(count(html, '<button type="button"')).toBe(5); // see, survey, one run, two surveys
  });
});

describe("what the overview leaves out", () => {
  it("has no new survey on the static demo and no agent items without a finished run", () => {
    expect(render({ canSurvey: false })).not.toContain("Survey your own streets");
    expect(render({ runs: [] })).not.toContain("New items");
  });
  it("says so when the survey has no city list, and drops the city-records card", () => {
    const none = render({ scan: { ...scan, city_check: undefined } as Scan });
    expect(none).toContain("This survey has no city list to compare.");
    expect(none).not.toContain("City records");
    expect(none).not.toContain("recorded assets");
    expect(none).toContain("GeoJSON and a CSV per item in the city&#x27;s own field names, and a survey report.");
    expect(none).toContain("Seongsu: Seoul street trees");
  });
  it("asks every possible defect again without a number when none was answered twice", () => {
    const once = render({ issues: issues.map((i) => ({ ...i, second_opinion: null })) });
    expect(once).toContain("<p>Muse Spark Open, an open-weight model, asks every possible defect again; when the two disagree the finding stays unclear</p>");
    expect(once).not.toContain("possible defects asked again");
  });
  it("shows only the opening and what needs no data while the example loads", () => {
    const loading = render({ scan: null, name: null, issues: [], counts: {}, examples: [], runs: [], others: [] });
    expect(loading).toContain("Street surveys without the fieldwork");
    expect(loading).toContain("Survey your own streets");
    expect(loading).not.toContain("$");
    expect(loading).not.toContain("See the finished survey");
    expect(loading).not.toContain("One pass, every item");
    expect(loading).not.toContain("How the pieces fit");
    expect(loading.replace(/<[^>]+>/g, "")).not.toMatch(/\d/); // no number in the text at all
  });
  it("keeps the check by eye to the survey it was measured on", () => {
    const other = render({ scan: { ...scan, id: 4 } as Scan });
    expect(other).not.toContain("Checked by eye");
    expect(other).not.toContain("73 of 86");
  });
  it("leaves out another city whose survey has no city list", () => {
    const html = render({ others: [{ scan: { ...seongsu, city_check: undefined } as Scan, name: "Seongsu" }] });
    expect(html).not.toContain("Another city");
    expect(html).toContain("Korean standard");
  });
});
