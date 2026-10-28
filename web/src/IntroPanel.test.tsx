import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import IntroPanel from "./IntroPanel";
import type { Scan } from "./types";

const scan = {
  id: 4, standard: "US", km: 0.94, usd_per_km: 4.11, processing_seconds: 1153, review_seconds: 64,
  items: [{ id: "utility_pole", target: "utility or streetlight pole", map_icon: "street-light", kind: "check", inventory: true },
    { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }],
} as unknown as Scan;
const art = { utility_pole: "data:pole", street_tree: "data:tree" };
const render = (over: Partial<Parameters<typeof IntroPanel>[0]> = {}) => renderToStaticMarkup(
  <IntroPanel scan={scan} name="Capitol Hill" art={art} canSurvey onSee={() => undefined} onSurvey={() => undefined}
    {...over} />);

describe("the introduction", () => {
  it("says what Fieldless does, its cost per km against walking, the four steps and the items", () => {
    const html = render();
    expect(html).toContain("Survey a city&#x27;s streets from photos that already exist");
    expect(html).toContain("$106");
    expect(html).toContain("$4.11");
    expect(html).toContain("20 min for 0.94 km, 1 min of it a person&#x27;s review");
    expect(html).toContain("Choose streets people have already photographed on Mapillary");
    expect(html).toContain("Utility or streetlight pole");
    expect(html).toContain("See the finished survey of Capitol Hill");
    expect(html).toContain("Survey your own streets");
  });
  it("leaves out what cannot be shown: a new survey on the static demo, a comparison without a published one", () => {
    expect(render({ canSurvey: false })).not.toContain("Survey your own streets");
    const kr = render({ scan: { ...scan, standard: "KR" } as Scan });
    expect(kr).not.toContain("$106");
    expect(kr).toContain("Survey a city");
    const none = render({ scan: null, name: null });
    expect(none).not.toContain("See the finished survey");
  });
});
