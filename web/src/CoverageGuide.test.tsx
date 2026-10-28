import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CoverageGuide from "./CoverageGuide";
import type { DemoArea } from "./types";

const demos: DemoArea[] = [{ scan_id: 4, name: "Seattle (Capitol Hill)", bbox: [-122.323, 47.613, -122.317, 47.6175] }];

describe("the new survey guide", () => {
  it("explains the green lines when the map draws them, and offers the example areas", () => {
    const html = renderToStaticMarkup(<CoverageGuide coverage demos={demos} onGo={() => undefined} />);
    expect(html).toContain("Street photos that already exist");
    expect(html).toContain("Green lines");
    expect(html).toContain("Street photo walks · Mapillary");
    expect(html).toContain(">Seattle (Capitol Hill)</button>");
  });
  it("drops the line key where the map has no coverage tiles (the static demo)", () => {
    const html = renderToStaticMarkup(<CoverageGuide coverage={false} demos={demos} onGo={() => undefined} />);
    expect(html).not.toContain("Green lines");
    expect(html).toContain("Draw a box over streets");
  });
});
