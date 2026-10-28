import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import IntroPanel, { IntroPhoto } from "./IntroPanel";
import type { Finding } from "./items";
import type { Scan } from "./types";

const scan = {
  id: 4, standard: "US", km: 0.94, usd_per_km: 4.11, processing_seconds: 1153, review_seconds: 64, image_count: 181,
  items: [{ id: "utility_pole", target: "utility or streetlight pole", map_icon: "street-light", kind: "check", inventory: true },
    { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }],
} as unknown as Scan;
const examples = [{ item: "utility_pole", url: "/api/evidence/p.jpg", box: [500, 100, 520, 600] },
  { item: "street_tree", url: "/api/evidence/t.jpg", box: null }];
const render = (over: Partial<Parameters<typeof IntroPanel>[0]> = {}) => renderToStaticMarkup(
  <IntroPanel scan={scan} name="Capitol Hill" examples={examples} canSurvey onSee={() => undefined}
    onSurvey={() => undefined} {...over} />);

describe("the introduction", () => {
  it("says what Fieldless does, its cost per km against walking with the source, and shows a photo per item", () => {
    const html = render();
    expect(html).toContain("Street surveys without the fieldwork");
    expect(html).toContain("$106");
    expect(html).toContain("$4.11");
    expect(html).toContain("Seattle&#x27;s 2017 sidewalk survey: $400,000 for 2,336 miles.");
    expect(html).toContain('alt="Utility or streetlight pole in a street photo"');
    expect(html).toContain("object-position:51% 35%");
    expect(html).toContain("Photos: Mapillary contributors, CC BY-SA 4.0");
    expect(html).toContain("See the finished survey of Capitol Hill");
    expect(html).toContain("Survey your own streets");
    expect(html).not.toContain("Choose streets people have already photographed");
  });
  it("leaves out what cannot be shown: a new survey on the static demo, a comparison without a published one", () => {
    expect(render({ canSurvey: false })).not.toContain("Survey your own streets");
    const kr = render({ scan: { ...scan, standard: "KR" } as Scan });
    expect(kr).not.toContain("$106");
    expect(kr).toContain("Street surveys without the fieldwork");
    const none = render({ scan: null, name: null, examples: [] });
    expect(none).not.toContain("See the finished survey");
    expect(none).not.toContain("Photos: Mapillary contributors");
  });
});

describe("the example photo over the introduction's map", () => {
  const finding = {
    key: "issue:156", item: "street_tree", lat: 0, lng: 0, heading: 0, box: [873, 0, 1000, 567], status: "auto", badge: null,
    title: "Street tree",
    picked: { kind: "issue", item: { id: 156, check: { model: "Muse Spark" }, evidence: {
      url: "/api/evidence/scan4_issue156.jpg", creator: "andresamaya18", captured_at: "2022-08-30T23:02:56Z",
      link: "https://www.mapillary.com/app/?pKey=1", license: "CC BY-SA 4.0", blurred: true } } },
  } as unknown as Finding;
  it("shows one of the example's photos with the model's box, what found and checked it, and its credit", () => {
    const html = renderToStaticMarkup(<IntroPhoto finding={finding} items={scan.items} photos={181} />);
    expect(html).toContain("One of this example&#x27;s 181 photos: SAM 3.1 found the street tree; Muse Spark checked it.");
    expect(html).toContain("left:87.3%");
    expect(html).toContain("andresamaya18");
  });
});
